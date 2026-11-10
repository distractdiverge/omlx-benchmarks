# 3. Benchmarks never leave a settings change behind

Date: 2026-11-09 · Status: Accepted

## Context
Astrid: "I don't want a benchmark to change the system from a run." Reading oMLX's
source: the throughput and accuracy benchmarks don't persist settings, but the
**context benchmark writes `max_context_window`** into the model's settings when it
finishes. Settings variants (A/B runs) also change per-model settings on purpose.

## Decision
Every step is wrapped in snapshot → (apply variant) → run → restore:
- Snapshot the model's settings (`GET /admin/api/models`) and global settings
  (`GET /admin/api/global-settings`) to `settings/snapshots/` **before** anything changes.
- Restore after the step whatever the outcome: pass, fail, cancel, Ctrl-C.
- Restore only the keys that differ from the snapshot, and send `null` where the
  snapshot had no value (oMLX treats an explicit null as "back to default").
- Every change and restore is appended to `settings/changes.log`.
- On startup, if a snapshot is marked unrestored (crash), restore it before running anything.
- The context benchmark's discovered value goes to the ledger, never to oMLX.
- The tool refuses to change host, port, API keys/auth or model directories.

## Consequences
- Running the tool leaves oMLX exactly as found, even across crashes.
- If Astrid *wants* a discovered context window applied, she sets it deliberately with
  `omlx-bench settings set`, which is logged like any other change.
