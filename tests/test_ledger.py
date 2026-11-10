import json
from omlx_bench.config import Paths
from omlx_bench.ledger import Ledger, export_payload
from omlx_bench.queue import Queue
from tests.fake_omlx import FakeOmlx


def _events(family, body):
    return FakeOmlx.default_events(FakeOmlx.__new__(FakeOmlx), family, body)


def test_perf_batch_step_flags_duplicate_single(tmp_path):
    p = Paths(tmp_path); q = Queue(tmp_path / "q.csv")
    s = q.add("m1", "performance", "batch4", "baseline",
              {"prompt_lengths": [1024], "batch_sizes": [4], "generation_length": 128})
    ev = _events("performance", s.body)
    ids = Ledger(p).record(s, 1, "b1", ev, None, {}, "", "t0")
    rows = Ledger(p).rows()
    assert ids == ["S0001-a1-1", "S0001-a1-2"]
    assert [r["test"] for r in rows] == ["pp1024/tg128", "batch4"]
    assert rows[0]["duplicate_of_single"] == "yes" and rows[1]["duplicate_of_single"] == ""
    assert rows[0]["omlx_ai_url"] == "https://omlx.ai/r/r1" and rows[0]["peak_mem_gb"] == "20.0"
    d = json.loads((tmp_path / rows[0]["details_path"]).read_text())
    assert d["request"]["model_id"] == "m1" and len(d["events"]) == len(ev)


def test_intelligence_writes_dashboard_export_with_suffix(tmp_path):
    p = Paths(tmp_path); q = Queue(tmp_path / "q.csv")
    s = q.add("org/m1", "intelligence", "mmlu_pro", "baseline", {"benchmarks": {"mmlu_pro": 1000}})
    body = dict(s.body); ev = _events("intelligence", body)
    Ledger(p).record(s, 1, "b1", ev, None, {}, "", "t0")
    Ledger(p).record(s, 2, "b2", ev, None, {}, "", "t0")
    rows = Ledger(p).rows()
    assert [r["export_path"] for r in rows] == ["results/exports/org_m1_mmlu_pro.json",
                                                "results/exports/org_m1_mmlu_pro-2.json"]
    exp = json.loads((tmp_path / rows[0]["export_path"]).read_text())
    # the keys the dashboard ingest script requires
    assert {"model_id", "benchmark", "correct", "total", "time_s", "thinking_used",
            "category_scores", "truncated_count", "finished_accuracy", "questions"} <= exp.keys()
    assert rows[0]["score"] == "60.0" and rows[0]["omlx_ai_url"] == "https://omlx.ai/i/1"


def test_context_row_and_failure_detail(tmp_path):
    p = Paths(tmp_path); q = Queue(tmp_path / "q.csv")
    s = q.add("m1", "context", "ctx32768", "baseline", {"target_tokens": 32768})
    ev = [{"type": "result", "data": {"target_tokens": 32768, "verified_tokens": 30720, "capped_by": "memory"}}]
    Ledger(p).record(s, 1, "b1", ev, None, {"temperature": 0.2}, "settings/snapshots/x.json", "t0")
    r = Ledger(p).rows()[0]
    assert (r["max_context"], r["capped_by"], r["settings_changed"]) == ("30720", "memory", '{"temperature": 0.2}')
    rel = Ledger(p).record_failure(s, 2, "b2", [], "boom", {}, "", "t0")
    assert json.loads((tmp_path / rel).read_text())["error"] == "boom"


def test_export_payload_matches_web_ui_shape():
    out = export_payload({"model_id": "m", "benchmark": "bbq", "question_results": [1], "external": False,
                          "truncated_count": 0, "finished_accuracy": None})
    assert list(out)[:9] == ["model_id", "benchmark", "accuracy", "correct", "total", "time_s",
                             "thinking_used", "category_scores", "questions"]
