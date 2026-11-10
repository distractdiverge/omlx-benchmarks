# 5. State lives in plain files: CSV for status and results, JSON for detail

Date: 2026-11-09 · Status: Accepted

## Context
Astrid wants (a) a file tracking planned runs with pass/fail for retries, and (b) a
file tracking every result, its runtime params, and where its details are. She also
wants her benchmark data local and owned, feeding her dashboard. Runs take hours and
the process can die (sleep, crash, Ctrl-C), so state must survive at every point.

## Decision
- `queue.csv`: one row per step. Columns: `step_id, model, suite, label, variant, settings,
  params, status, attempts, last_error, bench_id, result_ids, updated_at`.
  `status` ∈ pending · running · pass · fail. `params` is the exact request body
  (minus model_id) as compact JSON, so a step is reproducible from its row alone.
- `results/index.csv`: one row per result, with suite, model, variant, the settings it
  changed, exact params, headline metrics and the paths to its detail files.
- `results/details/<result_id>.json`: the full record: request, settings snapshot
  reference, every streamed event, final payload, timings, attempt number.
- `results/exports/<model_id>_<benchmark>.json`: intelligence results in exactly the
  format of oMLX's "Download JSON" (`model_id, benchmark, accuracy, correct, total,
  time_s, thinking_used, category_scores, questions, truncated_count, …`), with a
  `-2`, `-3` suffix on repeats, so the dashboard skill ingests them unchanged.
- Every write goes to a temp file then `os.replace`, so a crash never leaves a
  half-written CSV.
- The CSVs are written only by the tool; editing plan.toml and re-running `plan`
  is how the plan changes. Hand edits to `queue.csv` are tolerated but not required.

## Consequences
- Opens in Numbers or Excel, diffs in git, greps from a shell, no database.
- One writer at a time: running two `omlx-bench run` processes is unsupported (and
  oMLX would reject the second's benchmarks anyway). A lock file enforces it.
- CSV holds headline numbers only; analysis that needs per-question data reads
  the detail JSON.
