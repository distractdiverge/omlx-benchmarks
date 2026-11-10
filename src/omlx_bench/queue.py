"""queue.csv: every planned step and where it stands (ADR 0002, 0005).

A step's identity is (model, suite, variant, variant settings, params). The
variant's settings are frozen into the row, so editing a variant in plan.toml
later creates new steps instead of silently changing old ones. Re-planning only appends
steps whose identity is new; history is never rewritten.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from pathlib import Path

from .files import read_csv, write_csv

COLUMNS = ["step_id", "model", "suite", "label", "variant", "settings", "params", "status",
           "attempts", "last_error", "bench_id", "result_ids", "updated_at"]
STATUSES = {"pending", "running", "pass", "fail"}
SUITE_ORDER = {"performance": 0, "intelligence": 1, "context": 2}


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def canon(params: dict) -> str:
    return json.dumps(params, sort_keys=True, separators=(",", ":"))


@dataclass
class Step:
    step_id: str
    model: str
    suite: str
    label: str
    variant: str
    settings: str        # canonical JSON of the variant's per-model settings ("{}" = as-is)
    params: str          # canonical JSON request body without model_id
    status: str = "pending"
    attempts: int = 0
    last_error: str = ""
    bench_id: str = ""
    result_ids: str = ""
    updated_at: str = field(default_factory=now)

    @property
    def key(self) -> tuple:
        return (self.model, self.suite, self.variant, self.settings, self.params)

    @property
    def settings_dict(self) -> dict:
        return json.loads(self.settings or "{}")

    @property
    def body(self) -> dict:
        return {"model_id": self.model, **json.loads(self.params)}

    def runnable(self, max_attempts: int) -> bool:
        return self.status == "pending" or (self.status == "fail" and self.attempts < max_attempts)


class Queue:
    def __init__(self, path: Path):
        self.path = path
        self.steps: list[Step] = []
        for r in read_csv(path):
            r["attempts"] = int(r.get("attempts") or 0)
            if r.get("status") not in STATUSES:
                raise ValueError(f"{path}: step {r.get('step_id')} has unknown status {r.get('status')!r}")
            self.steps.append(Step(**{c: r.get(c, "") for c in COLUMNS}))

    def save(self) -> None:
        write_csv(self.path, COLUMNS, [asdict(s) for s in self.steps])

    def get(self, step_id: str) -> Step:
        for s in self.steps:
            if s.step_id == step_id:
                return s
        raise KeyError(f"No step {step_id} in {self.path.name}")

    def _next_id(self) -> str:
        n = max((int(s.step_id[1:]) for s in self.steps if s.step_id[1:].isdigit()), default=0)
        return f"S{n + 1:04d}"

    def add(self, model: str, suite: str, label: str, variant: str, params: dict,
            settings: dict | None = None) -> Step | None:
        """Append a step unless an identical one exists. Returns the new step or None."""
        p, st = canon(params), canon(settings or {})
        if any(s.key == (model, suite, variant, st, p) for s in self.steps):
            return None
        s = Step(self._next_id(), model, suite, label, variant, st, p)
        self.steps.append(s)
        return s

    def ordered(self, model_order: list[str]) -> list[Step]:
        """Model by model (plan order, unknown models last), then suite, then file order."""
        rank = {m: i for i, m in enumerate(model_order)}
        idx = {id(s): i for i, s in enumerate(self.steps)}
        return sorted(self.steps, key=lambda s: (rank.get(s.model, len(rank)), s.model,
                                                 SUITE_ORDER.get(s.suite, 9), idx[id(s)]))

    def mark(self, step: Step, status: str, **fields) -> None:
        assert status in STATUSES
        step.status = status
        for k, v in fields.items():
            setattr(step, k, v)
        step.updated_at = now()
        self.save()

    def reset(self, step_id: str) -> Step:
        s = self.get(step_id)
        self.mark(s, "pending", attempts=0, last_error="")
        return s

    def counts(self) -> dict[str, int]:
        out = {k: 0 for k in ("pending", "running", "pass", "fail")}
        for s in self.steps:
            out[s.status] += 1
        return out
