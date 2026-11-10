"""Result ledger (ADR 0005).

  results/index.csv              one row per result: params, headline metrics, paths
  results/details/<id>.json      everything about the attempt that produced it
  results/exports/<model>_<benchmark>[-N].json
                                 intelligence results in oMLX's own "Download JSON"
                                 format, for the dashboard skill
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from .config import Paths
from .files import read_csv, write_csv, write_json
from .queue import Step, now

COLUMNS = ["result_id", "finished_at", "suite", "model", "variant", "settings_changed", "step_id",
           "attempt", "bench_id", "label", "test", "params", "gen_tps", "processing_tps", "ttft_ms",
           "tpot_ms", "total_throughput", "peak_mem_gb", "score", "correct", "total", "thinking",
           "truncated", "max_context", "capped_by", "duplicate_of_single", "omlx_ai_url",
           "details_path", "export_path"]


def _r(x, n=2):
    return "" if x is None else round(float(x), n)


def safe_name(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", s)


def export_payload(r: dict) -> dict:
    """Mirror of oMLX web UI accDownloadResult(r, 'json') (apps/omlx-web dashboard.js)."""
    out = {"model_id": r.get("model_id"), "benchmark": r.get("benchmark"), "accuracy": r.get("accuracy"),
           "correct": r.get("correct"), "total": r.get("total"), "time_s": r.get("time_s"),
           "thinking_used": r.get("thinking_used") or False,
           "category_scores": r.get("category_scores") or None,
           "questions": r.get("question_results") or []}
    if r.get("external"):
        for k in ("valid_response_count", "empty_content_count", "truncated_count", "timeout_count",
                  "http_error_count", "connection_error_count", "invalid_response_count",
                  "parse_error_count", "wrong_count", "valid_response_rate", "valid_answer_accuracy",
                  "reliability_warning"):
            out[k] = r.get(k)
    elif r.get("truncated_count") is not None:
        for k in ("truncated_count", "truncated_correct_count", "finished_count", "finished_accuracy"):
            out[k] = r.get(k)
    return out


def _upload_url(events: list[dict], benchmark: str | None = None, pp: int | None = None) -> str:
    for ev in events:
        if ev.get("type") == "upload_done":
            for res in (ev.get("data") or {}).get("results") or []:
                if pp is None or res.get("context_length") == pp:
                    if res.get("url"):
                        return res["url"]
        if ev.get("type") == "upload":
            d = ev.get("data") or {}
            if (benchmark is None or d.get("benchmark") == benchmark) and d.get("url"):
                return d["url"]
    return ""


class Ledger:
    def __init__(self, paths: Paths):
        self.paths = paths

    def rows(self) -> list[dict]:
        return read_csv(self.paths.index)

    def _export_path(self, model: str, benchmark: str) -> Path:
        base = f"{safe_name(model)}_{safe_name(benchmark)}"
        p = self.paths.exports / f"{base}.json"
        n = 2
        while p.exists():
            p = self.paths.exports / f"{base}-{n}.json"
            n += 1
        return p

    def record(self, step: Step, attempt: int, bench_id: str, events: list[dict], final: dict | None,
               settings_changed: dict, snapshot_path: str, started_at: str) -> list[str]:
        """Write detail + index rows (+ exports) for a finished step. Returns result ids."""
        finished = now()
        results = [e["data"] for e in events if e.get("type") == "result" and isinstance(e.get("data"), dict)]
        base_id = f"{step.step_id}-a{attempt}"
        detail_rel = f"results/details/{base_id}.json"
        rows, ids = [], []
        params = json.loads(step.params)
        common = dict(finished_at=finished, suite=step.suite, model=step.model, variant=step.variant,
                      settings_changed=json.dumps(settings_changed, sort_keys=True) if settings_changed else "",
                      step_id=step.step_id, attempt=attempt, bench_id=bench_id, label=step.label,
                      params=step.params, details_path=detail_rel)
        for i, r in enumerate(results, 1):
            rid = f"{base_id}-{i}"
            row = dict(common, result_id=rid)
            if step.suite == "performance":
                is_batch = r.get("test_type") == "batch"
                row.update(test=f"batch{r.get('batch_size')}" if is_batch else f"pp{r.get('pp')}/tg{r.get('tg')}",
                           gen_tps=_r(r.get("gen_tps")), processing_tps=_r(r.get("processing_tps")),
                           ttft_ms=_r(r.get("ttft_ms"), 1), tpot_ms=_r(r.get("tpot_ms")),
                           total_throughput=_r(r.get("total_throughput")),
                           peak_mem_gb=_r((r.get("peak_memory_bytes") or 0) / 2**30) if r.get("peak_memory_bytes") else "",
                           duplicate_of_single="yes" if (params.get("batch_sizes") and not is_batch) else "",
                           omlx_ai_url=_upload_url(events, pp=None if is_batch else r.get("pp")))
            elif step.suite == "intelligence":
                export = self._export_path(r.get("model_id") or step.model, r.get("benchmark") or step.label)
                write_json(export, export_payload(r))
                row.update(test=r.get("benchmark"), score=_r((r.get("accuracy") or 0) * 100, 1),
                           correct=r.get("correct"), total=r.get("total"),
                           thinking="on" if r.get("thinking_used") else "off",
                           truncated=r.get("truncated_count", ""),
                           omlx_ai_url=_upload_url(events, benchmark=r.get("benchmark")),
                           export_path=str(export.relative_to(self.paths.root)))
            elif step.suite == "context":
                row.update(test=f"target{r.get('target_tokens')}", max_context=r.get("verified_tokens") or r.get("applied_tokens"),
                           capped_by=r.get("capped_by", ""))
            rows.append(row)
            ids.append(rid)
        write_json(self.paths.root / detail_rel, {
            "step": {"step_id": step.step_id, "model": step.model, "suite": step.suite, "label": step.label,
                     "variant": step.variant, "attempt": attempt},
            "request": step.body, "bench_id": bench_id, "started_at": started_at, "finished_at": finished,
            "settings_changed": settings_changed, "settings_snapshot": snapshot_path,
            "result_ids": ids, "final": final, "events": events})
        existing = self.rows()
        write_csv(self.paths.index, COLUMNS, existing + rows)
        return ids

    def record_failure(self, step: Step, attempt: int, bench_id: str, events: list[dict], error: str,
                       settings_changed: dict, snapshot_path: str, started_at: str) -> str:
        """Failed attempts keep their detail file (no index row) for debugging."""
        rel = f"results/details/{step.step_id}-a{attempt}-FAILED.json"
        write_json(self.paths.root / rel, {
            "step": {"step_id": step.step_id, "model": step.model, "suite": step.suite, "label": step.label,
                     "variant": step.variant, "attempt": attempt},
            "request": step.body, "bench_id": bench_id, "started_at": started_at, "failed_at": now(),
            "error": error, "settings_changed": settings_changed, "settings_snapshot": snapshot_path,
            "events": events})
        return rel
