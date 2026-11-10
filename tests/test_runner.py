import json
import os
import pytest
from omlx_bench.client import Client
from omlx_bench.config import Config, Paths
from omlx_bench.queue import Queue
from omlx_bench.runner import Runner, StopRun
from omlx_bench import steps
from tests.fake_omlx import KEY


def make(tmp_path, fake, raw, out=None, sleep=None, url=None):
    (tmp_path / "plan.toml").write_text("")
    cfg = Config(url or fake.url, KEY, Paths(tmp_path))
    q = Queue(cfg.paths.queue)
    plan = steps.build(raw)
    for s in plan.steps:
        q.add(s.model, s.suite, s.label, s.variant, s.params, s.settings)
    q.save()
    log = []
    r = Runner(cfg, Client(cfg.url, KEY), out=out or log.append, sleep=sleep or (lambda s: None))
    return r, plan, log


FULL = {"models": ["m1"], "performance": {"prompt_lengths": [1024], "batch_sizes": [4]},
        "intelligence": {"suites": {"mmlu_pro": 1000}}, "context": {"target_tokens": [32768]}}


def test_happy_path_runs_every_step_and_leaves_no_trace(tmp_path, fake):
    r, plan, log = make(tmp_path, fake, FULL)
    assert r.run(plan.models, 3) == {"pass": 4, "fail": 0}
    q = Queue(tmp_path / "queue.csv")
    assert [s.status for s in q.steps] == ["pass"] * 4
    rows = r.ledger.rows()
    assert len(rows) == 5  # pp1024, batch4 (dup single + batch), mmlu_pro, ctx
    assert fake.models["m1"]["max_context_window"] is None  # context bench write undone (ADR 0003)
    assert (tmp_path / "results/exports/m1_mmlu_pro.json").exists()
    assert not (tmp_path / ".omlx-bench.lock").exists()
    assert all(json.loads(p.read_text())["restored"] for p in (tmp_path / "settings/snapshots").glob("*.json"))
    starts = [c[2] for c in fake.calls if c[0] == "POST" and "start" in c[1] or c[0] == "POST" and "queue/add" in c[1]]
    assert len(starts) == 4 and all(len(b.get("prompt_lengths", [0])) == 1 for b in starts)


def test_error_event_costs_an_attempt_and_retries_are_capped(tmp_path, fake):
    r, plan, log = make(tmp_path, fake, {"models": ["m1"], "context": {"target_tokens": [32768]}})
    for attempt in (1, 2, 3):
        fake.script = [[{"type": "progress"}, {"type": "error", "message": "Memory Guard is disabled"}]]
        r = Runner(r.cfg, r.client, out=log.append, sleep=lambda s: None)
        assert r.run(plan.models, 3) == {"pass": 0, "fail": 1}
        s = Queue(tmp_path / "queue.csv").steps[0]
        assert (s.status, s.attempts, s.last_error) == ("fail", attempt, "Memory Guard is disabled")
        assert (tmp_path / f"results/details/S0001-a{attempt}-FAILED.json").exists()
    r = Runner(r.cfg, r.client, out=log.append, sleep=lambda s: None)
    assert r.run(plan.models, 3) == {"pass": 0, "fail": 0}  # exhausted: needs `retry`


def test_rejected_request_fails_the_step(tmp_path, fake):
    fake.script = [("http", 400, "Invalid target 99")]
    r, plan, log = make(tmp_path, fake, {"models": ["m1"], "context": {"target_tokens": [32768]}})
    r.run(plan.models, 3)
    assert "HTTP 400" in Queue(tmp_path / "queue.csv").steps[0].last_error


def test_dropped_stream_reattaches_without_costing_an_attempt(tmp_path, fake):
    r, plan, log = make(tmp_path, fake, {"models": ["m1"], "context": {"target_tokens": [32768]}})
    fake.drop_stream_once.add("b1")
    assert r.run(plan.models, 3) == {"pass": 1, "fail": 0}
    assert Queue(tmp_path / "queue.csv").steps[0].attempts == 1
    assert any("re-attaching" in l for l in log)
    assert len(r.ledger.rows()) == 1  # replayed events not double-counted


def test_waits_while_oMLX_is_busy_with_something_else(tmp_path, fake):
    fake.running["performance"] = "foreign"
    fake.runs["foreign"] = {"family": "performance", "events": [], "body": {"model_id": "x"}}
    r, plan, log = make(tmp_path, fake, {"models": ["m1"], "context": {"target_tokens": [32768]}},
                        sleep=lambda s: fake.running.clear())
    assert r.run(plan.models, 3) == {"pass": 1, "fail": 0}
    assert any("busy" in l for l in log)


def test_unreachable_server_stops_without_charging(tmp_path, fake):
    r, plan, log = make(tmp_path, fake, {"models": ["m1"], "context": {"target_tokens": [32768]}},
                        url="http://127.0.0.1:9")
    with pytest.raises(StopRun):
        r.run(plan.models, 3)
    s = Queue(tmp_path / "queue.csv").steps[0]
    assert (s.status, s.attempts) == ("pending", 0)


def test_variant_is_applied_before_start_and_restored_after(tmp_path, fake):
    raw = {"models": ["m2"], "variants": {"use": ["cold"], "cold": {"temperature": 0.0}},
           "context": {"target_tokens": [32768]}}
    r, plan, log = make(tmp_path, fake, raw)
    r.run(plan.models, 3)
    seq = [(c[0], c[1].split("/")[-1], c[2]) for c in fake.calls if c[0] in ("PUT", "POST")]
    assert seq[0] == ("PUT", "settings", {"temperature": 0.0})
    assert seq[1][1] == "start"
    assert fake.models["m2"] == {"max_context_window": 65536, "temperature": 0.7}
    assert json.loads(r.ledger.rows()[0]["settings_changed"]) == {"temperature": 0.0}


def test_ctrl_c_cancels_on_server_and_does_not_charge(tmp_path, fake):
    fake.script = [[{"type": "progress", "message": "Measuring…"}, {"type": "progress", "message": "more"},
                    {"type": "result", "data": {}}, {"type": "done"}]]

    def out(line):
        if "Measuring" in line:
            raise KeyboardInterrupt
    r, plan, log = make(tmp_path, fake, {"models": ["m1"], "context": {"target_tokens": [32768]}}, out=out)
    with pytest.raises(KeyboardInterrupt):
        r.run(plan.models, 3)
    s = Queue(tmp_path / "queue.csv").steps[0]
    assert (s.status, s.attempts, s.last_error) == ("pending", 0, "cancelled by user")
    assert any(c[1].endswith("/cancel") for c in fake.calls)
    assert not (tmp_path / ".omlx-bench.lock").exists()


def test_crash_recovery_restores_settings_and_fails_orphaned_step(tmp_path, fake):
    r, plan, log = make(tmp_path, fake, {"models": ["m1"], "context": {"target_tokens": [32768]}})
    snap = r.settings.snapshot("m1", "crashed run")
    fake.models["m1"]["temperature"] = 1.5          # change left behind by the crash
    q = Queue(tmp_path / "queue.csv"); q.mark(q.steps[0], "running", attempts=1, bench_id="gone")
    r = Runner(r.cfg, r.client, out=log.append, sleep=lambda s: None)
    r.run(plan.models, 3)
    assert fake.models["m1"]["temperature"] is None
    assert json.loads(snap.read_text())["restored"] is True
    s = Queue(tmp_path / "queue.csv").steps[0]
    assert (s.status, s.attempts) == ("pass", 2)   # orphan failed (attempt 1), then retried and passed


def test_second_runner_is_blocked_by_lock(tmp_path, fake):
    r, plan, log = make(tmp_path, fake, {"models": ["m1"], "context": {"target_tokens": [32768]}})
    (tmp_path / ".omlx-bench.lock").write_text(str(os.getpid()))
    with pytest.raises(StopRun, match="only one runner"):
        r.run(plan.models, 3)


def test_stale_lock_is_taken_over(tmp_path, fake):
    r, plan, log = make(tmp_path, fake, {"models": ["m1"], "context": {"target_tokens": [32768]}})
    (tmp_path / ".omlx-bench.lock").write_text("999999")
    assert r.run(plan.models, 3)["pass"] == 1
