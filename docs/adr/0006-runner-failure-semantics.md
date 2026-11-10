# 6. Runner failure semantics: what costs an attempt and what doesn't

Date: 2026-11-09 · Status: Accepted

## Context
Retries are capped (`run.max_attempts`, default 3) so a broken step can't loop forever.
But many interruptions say nothing about the step itself: the Mac slept, oMLX was
restarted, Astrid pressed Ctrl-C, or she started something from the dashboard. If those
burned attempts, a night of bad luck would exhaust good steps.

## Decision
**Costs an attempt (step → `fail`, retried on the next run while attempts remain):**
- oMLX rejects the request (400/404/422: bad params, model not found, Memory Guard off).
- The benchmark ends with an `error` event, or ends without any result.
- Settings for the variant can't be applied.

**Does not cost an attempt:**
- **Stream dropped** (`StreamLost`): re-attach to the same bench_id (oMLX replays all
  events) up to 5 times, 10 s apart. Only if the run is gone does it fall to the rules here.
- **oMLX busy** (409, or `active` shows a run that isn't ours): wait and poll every 30 s.
- **oMLX unreachable**: the step goes back to `pending` and the whole run stops, since
  every following step would fail the same way.
- **Ctrl-C**: cancel the benchmark on the server, restore settings, step back to
  `pending`, stop.

**Always, in a `finally`:** restore the settings snapshot (ADR 0003). If the restore
itself fails, the run stops immediately: continuing would stack changes on an unknown
state.

**Crash recovery at startup:** restore any unrestored snapshot; for a step left
`running`, re-attach if oMLX still shows its bench_id running, else mark it `fail`
(that attempt was already counted when it started).

**One runner at a time:** a lock file (`.omlx-bench.lock`, holding the PID) blocks a
second `run`; a stale lock from a dead PID is taken over.

## Consequences
- `attempts` counts real failures only, so `fail` after 3 means "look at this step".
- An interrupted step re-runs from the start: oMLX can't resume a partial benchmark.
