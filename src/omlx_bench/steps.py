"""plan.toml -> single-test steps (ADR 0002).

Allowed values are copied from oMLX's validators (commit 2165e75) so a typo fails
at `plan` time, not as a failed step hours into a run.
"""
from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

# omlx/admin/benchmark.py
PROMPT_LENGTHS = {1024, 4096, 8192, 16384, 32768, 65536, 131072, 200000}
BATCH_SIZES = {2, 4, 8}
CONTEXT_PROFILES = {"code_python", "code_mixed", "novel_ko", "novel_en", "novel_ja"}
WARMUP_MODES = {"quick", "ane_2048"}
BATCH_PP = 1024  # batch tests always use pp1024
# omlx/admin/accuracy_benchmark.py
SUITES = {"mmlu", "mmlu_pro", "kmmlu", "cmmlu", "jmmlu", "hellaswag", "truthfulqa", "arc_challenge",
          "winogrande", "gsm8k", "mathqa", "humaneval", "mbpp", "livecodebench", "bbq", "safetybench"}
ACCURACY_BATCH = {1, 2, 4, 8, 16, 32}
SAMPLING = {"deterministic", "model_settings"}
# omlx/admin/context_benchmark.py
CONTEXT_TARGETS = {16384, 32768, 65536, 131072, 262144, 524288}


class PlanError(Exception):
    pass


@dataclass
class PlannedStep:
    model: str
    suite: str
    label: str
    variant: str
    settings: dict
    params: dict


@dataclass
class Plan:
    models: list[str]
    variants: dict[str, dict]
    steps: list[PlannedStep] = field(default_factory=list)
    max_attempts: int = 3


def _check(cond: bool, msg: str) -> None:
    if not cond:
        raise PlanError(msg)


def _subset(name: str, values, allowed: set) -> list:
    _check(isinstance(values, list), f"{name} must be a list")
    bad = [v for v in values if v not in allowed]
    _check(not bad, f"{name}: {bad} not allowed by oMLX; choose from {sorted(allowed)}")
    return values


def _performance_templates(perf: dict) -> list[tuple[str, str, dict]]:
    """Expand [performance] into (suite, label, params) templates."""
    tg = perf.get("generation_length", 128)
    _check(isinstance(tg, int) and tg > 0, "performance.generation_length must be a positive integer")
    prof = perf.get("context_profile", "code_python")
    _check(prof in CONTEXT_PROFILES, f"performance.context_profile must be one of {sorted(CONTEXT_PROFILES)}")
    warm = perf.get("warmup_mode", "quick")
    _check(warm in WARMUP_MODES, f"performance.warmup_mode must be one of {sorted(WARMUP_MODES)}")
    common = {"generation_length": tg, "context_profile": prof, "warmup_mode": warm}
    out: list[tuple[str, str, dict]] = []
    for pp in _subset("performance.prompt_lengths", perf.get("prompt_lengths", []), PROMPT_LENGTHS):
        out.append(("performance", f"pp{pp}", {"prompt_lengths": [pp], "batch_sizes": [], **common}))
    for bs in _subset("performance.batch_sizes", perf.get("batch_sizes", []), BATCH_SIZES):
        out.append(("performance", f"batch{bs}",
                    {"prompt_lengths": [BATCH_PP], "batch_sizes": [bs], **common}))
    return out


def _intelligence_templates(intel: dict) -> list[tuple[str, str, dict]]:
    """Expand [intelligence] into (suite, label, params) templates."""
    suites = intel.get("suites") or {}
    _check(isinstance(suites, dict), "intelligence.suites must be a table of suite = sample_size")
    _subset("intelligence.suites", list(suites), SUITES)
    think = intel.get("enable_thinking", False)
    _check(isinstance(think, bool), "intelligence.enable_thinking must be true or false")
    samp = intel.get("sampling_profile", "deterministic")
    _check(samp in SAMPLING, f"intelligence.sampling_profile must be one of {sorted(SAMPLING)}")
    bsz = intel.get("batch_size", 1)
    _check(bsz in ACCURACY_BATCH, f"intelligence.batch_size must be one of {sorted(ACCURACY_BATCH)}")
    out: list[tuple[str, str, dict]] = []
    for suite, n in suites.items():
        _check(isinstance(n, int) and n >= 0, f"intelligence.suites.{suite} must be an integer >= 0 (0 = whole dataset)")
        out.append(("intelligence", suite, {"benchmarks": {suite: n}, "batch_size": bsz,
                                             "enable_thinking": think, "sampling_profile": samp}))
    return out


def _context_templates(ctx: dict) -> list[tuple[str, str, dict]]:
    """Expand [context] into (suite, label, params) templates."""
    out: list[tuple[str, str, dict]] = []
    for t in _subset("context.target_tokens", ctx.get("target_tokens", []), CONTEXT_TARGETS):
        out.append(("context", f"ctx{t}", {"target_tokens": t}))
    return out


def load(path: Path) -> Plan:
    try:
        raw = tomllib.loads(path.read_text())
    except tomllib.TOMLDecodeError as e:
        raise PlanError(f"{path.name}: {e}") from None
    return build(raw)


def build(raw: dict) -> Plan:
    models = raw.get("models") or []
    _check(isinstance(models, list) and all(isinstance(m, str) and m for m in models),
           "models must be a list of model id strings")
    _check(len(set(models)) == len(models), "models has duplicates")

    vt = dict(raw.get("variants") or {})
    use = vt.pop("use", ["baseline"])
    vt.setdefault("baseline", {})
    for name in use:
        _check(name in vt, f"variants.use names {name!r} but no such variant is defined")
        _check(isinstance(vt[name], dict), f"variant {name!r} must be a table of setting = value")
    from .settings import FORBIDDEN_MODEL_KEYS  # guardrail shared with `settings set`
    for name in use:
        bad = FORBIDDEN_MODEL_KEYS & set(vt[name])
        _check(not bad, f"variant {name!r} sets {sorted(bad)}, which the tool never changes")
    variants = {n: vt[n] for n in use}

    perf, intel, ctx = raw.get("performance") or {}, raw.get("intelligence") or {}, raw.get("context") or {}
    templates: list[tuple[str, str, dict]] = []  # (suite, label, params)

    templates.extend(_performance_templates(perf))
    templates.extend(_intelligence_templates(intel))
    templates.extend(_context_templates(ctx))

    run = raw.get("run") or {}
    ma = run.get("max_attempts", 3)
    _check(isinstance(ma, int) and ma >= 1, "run.max_attempts must be an integer >= 1")

    steps = [PlannedStep(m, suite, label, vname, vset, params)
             for m in models for vname, vset in variants.items() for suite, label, params in templates]
    return Plan(models, variants, steps, ma)
