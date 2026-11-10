import os
import pytest
from omlx_bench import cli
from omlx_bench.queue import Queue
from tests.fake_omlx import KEY

PLAN = '''models = ["m1"]
[performance]
prompt_lengths = [1024]
[context]
target_tokens = [32768]
'''


@pytest.fixture
def proj(tmp_path, fake, monkeypatch):
    (tmp_path / "plan.toml").write_text(PLAN)
    (tmp_path / ".env").write_text(f"OMLX_API_KEY={KEY}\nOMLX_URL={fake.url}\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("OMLX_API_KEY", raising=False); monkeypatch.delenv("OMLX_URL", raising=False)
    return tmp_path


def test_plan_run_status_end_to_end(proj, capsys):
    assert cli.main(["plan"]) == 0
    assert cli.main(["plan"]) == 0  # idempotent
    assert "Added 0 step(s); 2 already planned" in capsys.readouterr().out
    assert cli.main(["run", "--no-keep-awake"]) == 0
    assert cli.main(["status"]) == 0
    assert "2 pass" in capsys.readouterr().out


def test_plan_rejects_unknown_model(proj, capsys):
    (proj / "plan.toml").write_text('models = ["nope"]\n[context]\ntarget_tokens=[32768]\n')
    assert cli.main(["plan"]) == 1 and "Not on the oMLX server" in capsys.readouterr().out


def test_failure_shows_in_status_and_retry_resets(proj, fake, capsys):
    cli.main(["plan"])
    fake.script = [("http", 400, "bad"), ("http", 400, "bad")]
    assert cli.main(["run", "--no-keep-awake"]) == 2
    cli.main(["status"])
    assert "S0001" in capsys.readouterr().out
    assert cli.main(["retry", "--all-failed"]) == 0
    assert all(s.status == "pending" for s in Queue(proj / "queue.csv").steps)


def test_manual_settings_change_is_kept_and_undoable(proj, fake, capsys):
    assert cli.main(["settings", "set", "m2", "temperature=0.1"]) == 0
    out = capsys.readouterr().out
    assert fake.models["m2"]["temperature"] == 0.1
    snap = out.strip().split()[-1]
    assert cli.main(["run", "--no-keep-awake"]) == 0  # recovery must NOT undo a deliberate change
    assert fake.models["m2"]["temperature"] == 0.1
    assert cli.main(["settings", "restore", snap]) == 0
    assert fake.models["m2"]["temperature"] == 0.7


def test_settings_guardrail_message(proj, capsys):
    assert cli.main(["settings", "global", "port=9"]) == 1
    assert "off limits" in capsys.readouterr().out


def test_missing_key_message(proj, capsys):
    (proj / ".env").write_text("OMLX_API_KEY=\n")
    assert cli.main(["status"]) == 1 and "MAIN" in capsys.readouterr().out
