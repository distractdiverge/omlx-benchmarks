# omlx-bench — plan (v2)

Status: **built 2026-11-09 (58 tests passing against a fake oMLX); next: smoke test on the Mac.** Design rationale lives in `docs/adr/`.
API facts verified against oMLX source, commit 2165e75 (Nov 5 2026).

## Goal
One `uv`-run tool that drives oMLX **performance, intelligence and context** benchmarks through the admin API.
- **One test per API call** ("step"), so a failure loses one test, not a whole sweep.
- Every step and its pass/fail live in `queue.csv`. Failed steps get retried.
- Every result lives in `results/index.csv` with its params and a pointer to full detail.
- The Mac stays awake for the whole run (Amphetamine, or `caffeinate` as a fallback).

## Project layout (scaffolded)
| Path | What |
|---|---|
| `pyproject.toml`, `.python-version` | uv project, Python 3.12, deps: httpx, typer, rich. Run everything with `uv run omlx-bench …` |
| `.env` / `.env.example` | `OMLX_API_KEY` (must be the **main** key), `OMLX_URL=http://localhost:8000`. `.env` is git-ignored. |
| `plan.toml` | What you want run: models × performance / intelligence / context settings. Hand-edited. |
| `queue.csv` | Generated from plan.toml: one row per step, with status, attempts, last error, bench_id, result link. |
| `results/index.csv` | One row per result: suite, model, exact params, headline metrics, omlx.ai URL, details path. |
| `results/details/<result_id>.json` | Full request body, every streamed event, final server payload, timings, attempt number. |
| `settings/` | Snapshots of settings before any change, plus a change log |
| `src/omlx_bench/` | `settings.py` snapshot/apply/restore · `cli.py` commands · `client.py` API · `steps.py` plan→requests · `queue.py` · `ledger.py` · `keepawake.py` |

## What one "step" is, per suite
| Suite | oMLX endpoint | One step = | Notes |
|---|---|---|---|
| Performance | `POST /admin/api/bench/start` | one prompt length (`prompt_lengths=[N]`, no batch) **or** one batch size | oMLX requires a prompt length on every run, and batch tests always use pp1024. So a batch step = pp1024 single + one batch size. That duplicate pp1024 row is recorded but flagged. |
| Intelligence | `POST /admin/api/bench/accuracy/queue/add` | one suite (e.g. `{"mmlu": 200}`) | 16 suites available: mmlu, mmlu_pro, gsm8k, humaneval, mbpp, livecodebench, arc_challenge, hellaswag, winogrande, truthfulqa, mathqa, bbq, safetybench, kmmlu/cmmlu/jmmlu. Deterministic (temp 0) by default. We add one item and wait for it; the server's queue is never stacked. |
| Context | `POST /admin/api/bench/context/start` | one target size (16k, 32k, 64k, 128k, 256k or 512k) | Finds the largest context that fits in memory. **Requires Memory Guard on.** **Side effect: writes the result into that model's `max_context_window` setting.** See decision 3. |

The server allows only one benchmark of any kind at a time, so steps run strictly in sequence. oMLX loads the model at the start of each step and unloads it after, so per-step load time is the price of the isolation you asked for. Order: model by model, performance → intelligence → context.

## queue.csv lifecycle
`pending` → `running` → `pass` | `fail`
- Each `run` processes `pending` steps, plus `fail` steps that still have attempts left (`max_attempts`, default 3). It does them in order and keeps going past failures.
- A failure records the error and the server's bench_id.
- If the script dies mid-step: on the next start, any `running` row is checked against the server. If that bench is still going, the tool re-attaches to it. Otherwise the step is marked failed and retried.
- Ctrl-C cancels the in-flight step on the server and marks it `fail` ("cancelled by user").
- `omlx-bench retry <step_id>` resets a step that used up its attempts. `omlx-bench status` prints counts plus the list of failures.
- Re-running `omlx-bench plan` after editing plan.toml only **adds** new steps and never rewrites history. Changed params count as a new step.

## Keeping the Mac awake (Amphetamine)
- While `run` is active, if Amphetamine is running, the tool starts an Amphetamine session via AppleScript (`osascript`) and ends it when the run finishes. If you already had a session going, it leaves that one alone.
- If Amphetamine isn't running, it falls back to macOS's built-in `caffeinate -i -w <pid>`, which ends automatically when the script exits.
- Alternative with zero code: an Amphetamine **Trigger** that activates while a given app/process is running, pointed at the tool's process. I'll verify the exact AppleScript commands and Trigger options on your Mac during the smoke test rather than trust my memory.
- Caveat: if you close the lid on battery, `caffeinate` can't stop the Mac sleeping; Amphetamine's closed-display mode can.

## Settings control (added)
oMLX exposes its settings through the same admin API (main key):
| Endpoint | What |
|---|---|
| `GET /admin/api/models` | All models **with their current settings** |
| `PUT /admin/api/models/{id}/settings` | Change per-model settings: context window, sampling (temp/top_p/top_k/min_p/penalties), thinking on/off and budget, TurboQuant KV compression, MTP, ANE prefill, GPU power limit, TTL, etc. Unknown fields are rejected. |
| `POST /admin/api/models/{id}/settings/reset` | Back to defaults |
| `GET/POST /admin/api/models/{id}/settings/optimal` | omlx.ai's suggested optimal settings for your hardware |
| `…/profiles` (list/create/update/delete/apply) | Named per-model settings profiles |
| `GET/POST /admin/api/global-settings` | Server-wide: Memory Guard tier and ceiling, cache, scheduler (chunked prefill, prefill priority, concurrency), burst decode, etc. |

How the tool will use it:
- **Variants in plan.toml.** You define named settings sets, e.g. `baseline = {}` and `tq4 = {turboquant_kv_enabled = true, turboquant_kv_bits = 4}`. Each step becomes model × variant × one test, so A/B comparisons are just more queue rows.
- **Snapshot → apply → run → restore.** Before the first change, the tool saves the model's (and global) settings to `settings/snapshots/<timestamp>.json`. It applies the variant, runs the step, then restores. Restore also happens on Ctrl-C and on failure. If the script crashes, `omlx-bench settings restore` puts things back from the last snapshot.
- **Every result records the exact settings it ran under** (full settings in the details JSON; variant name + changed keys in index.csv). Results stay comparable months later.
- **Standalone commands too:** `settings show <model>`, `settings set <model> key=value …`, `settings global …`, `settings restore`. I can use these directly when you ask, e.g. "turn on Memory Guard" before context runs.
- **Guardrails:** the tool refuses to touch server-binding and access settings (host, port, auth/API keys, model directories). Those need a restart or could lock us out. Everything it changes is logged in `settings/changes.log`.
- The context benchmark's `max_context_window` write is restored like any other change (decision 3).

## omlx.ai sharing
Performance and intelligence results are automatically uploaded to the omlx.ai community board after each step, as when you run them from the dashboard. Context results are not. Upload URLs and IDs get saved in the ledger. An upload failure doesn't fail the step.

## Decisions (settled 2026-11-09)
1. Main API key is in `.env` (git-ignored, never staged off the Mac).
2. Auto-upload to omlx.ai for perf + intelligence: **OK for now**.
3. **No benchmark may leave a system change behind.** Every step snapshots settings first and restores them after, whether it passes, fails or is cancelled. This includes the context benchmark's `max_context_window` write. There is no `keep_context_result` switch; the discovered value lives in the ledger, not in oMLX.
4. Intelligence suites follow the benchmarking notes and the dashboard: **MMLU-Pro, ARC-Challenge, HumanEval, MBPP, TruthfulQA, BBQ**. No GSM8K. Sample sizes match the dashboard's "full" sizes (MMLU-Pro 1000, ARC-C 300, HumanEval 164, MBPP 500, TruthfulQA 817, BBQ 1000), because it drops runs under 150 questions. LiveCodeBench (1,054 questions, 8+ hours) is listed but commented out.
5. **Model by model:** finish every step for one model before starting the next.
6. Each intelligence step also writes `results/exports/<model_id>_<benchmark>.json` in exactly the format of oMLX's own Download JSON, so the dashboard skill ingests it unchanged. The run date comes from `results/index.csv`.

## Build order
- [x] Config, API client (SSE), queue.csv, ledger + dashboard exports
- [x] Plan expansion with up-front validation; settings snapshot/apply/restore
- [x] Runner (ADR 0006 failure rules), keep-awake, CLI
- [x] End-to-end test of the shipped defaults against a fake server
- [ ] `uv sync` on the Mac, commit `uv.lock`
- [ ] Smoke test: one small model, `run --limit` through one step of each suite
- [ ] Feed a real export through the dashboard skill

## Out of scope for now
ANE tuning, external (remote) endpoints, git commits (you commit).
