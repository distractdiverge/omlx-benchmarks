"""The shipped plan.toml defaults, two models, through the real CLI against the fake server."""
import json
import pytest
from pathlib import Path
from omlx_bench import cli
from omlx_bench.queue import Queue
from tests.fake_omlx import KEY

SHIPPED = (Path(__file__).parents[1] / "plan.toml").read_text()


@pytest.fixture
def proj(tmp_path, fake, monkeypatch):
    (tmp_path / "plan.toml").write_text(SHIPPED.replace("models = [", 'models = ["m2", "m1",', 1))
    (tmp_path / ".env").write_text(f"OMLX_API_KEY={KEY}\nOMLX_URL={fake.url}\n")
    monkeypatch.chdir(tmp_path)
    for k in ("OMLX_API_KEY", "OMLX_URL"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path


def test_shipped_defaults_two_models(proj, fake, capsys):
    before = {k: dict(v) for k, v in fake.models.items()}
    assert cli.main(["plan"]) == 0
    q = Queue(proj / "queue.csv")
    per_model = 3 + 3 + 6 + 2  # pp 1k/4k/8k, batch 2/4/8, six suites, two context targets
    assert len(q.steps) == 2 * per_model
    # one model at a time, in plan order, never interleaved
    order = [s.model for s in q.ordered(["m2", "m1"])]
    assert order == ["m2"] * per_model + ["m1"] * per_model
    assert cli.main(["run", "--no-keep-awake"]) == 0
    assert fake.models == before                          # ADR 0003: no trace left
    starts = [c for c in fake.calls if c[0] == "POST" and c[1].endswith(("/start", "/queue/add"))]
    assert [c[2]["model_id"] for c in starts] == order    # the server saw exactly that order
    exports = sorted(p.name for p in (proj / "results/exports").iterdir())
    assert exports == sorted(f"{m}_{s}.json" for m in ("m1", "m2")
                             for s in ("mmlu_pro", "arc_challenge", "humaneval", "mbpp", "truthfulqa", "bbq"))
    sizes = {c[2]["benchmarks"].popitem()[1] for c in starts if "benchmarks" in c[2]}
    assert sizes == {1000, 300, 164, 500, 817}            # dashboard "full" sizes, none under 150
