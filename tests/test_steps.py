import pytest
from pathlib import Path
from omlx_bench import steps


def test_shipped_plan_toml_is_valid_and_has_no_models_yet():
    p = steps.load(Path(__file__).parents[1] / "plan.toml")
    assert p.models == [] and p.steps == [] and p.max_attempts == 3


def test_expansion_is_one_test_per_step():
    raw = {"models": ["a", "b"], "variants": {"use": ["baseline", "tq"], "tq": {"turboquant_kv_bits": 4}},
           "performance": {"prompt_lengths": [1024, 4096], "batch_sizes": [4]},
           "intelligence": {"suites": {"mmlu_pro": 1000, "bbq": 1000}},
           "context": {"target_tokens": [32768]}}
    p = steps.build(raw)
    assert len(p.steps) == 2 * 2 * (3 + 2 + 1)
    first = [(s.suite, s.label) for s in p.steps if s.model == "a" and s.variant == "baseline"]
    assert first == [("performance", "pp1024"), ("performance", "pp4096"), ("performance", "batch4"),
                     ("intelligence", "mmlu_pro"), ("intelligence", "bbq"), ("context", "ctx32768")]
    batch = next(s for s in p.steps if s.label == "batch4")
    assert batch.params["prompt_lengths"] == [1024] and batch.params["batch_sizes"] == [4]
    assert all(len(s.params.get("benchmarks", {"x": 0})) == 1 for s in p.steps)
    assert next(s for s in p.steps if s.variant == "tq").settings == {"turboquant_kv_bits": 4}


@pytest.mark.parametrize("raw,msg", [
    ({"models": ["a"], "performance": {"prompt_lengths": [2048]}}, "2048"),
    ({"models": ["a"], "intelligence": {"suites": {"gsm9k": 10}}}, "gsm9k"),
    ({"models": ["a"], "context": {"target_tokens": [100000]}}, "100000"),
    ({"models": ["a"], "variants": {"use": ["nope"]}}, "nope"),
    ({"models": ["a", "a"]}, "duplicates"),
    ({"models": ["a"], "variants": {"use": ["x"], "x": {"model_alias": "y"}}}, "never changes"),
])
def test_bad_plans_fail_at_plan_time(raw, msg):
    with pytest.raises(steps.PlanError, match=msg):
        steps.build(raw)
