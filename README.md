# omlx-bench

Runs oMLX **performance, intelligence and context** benchmarks one test at a time
through oMLX's admin API. It tracks every step and result in plain files, keeps the
Mac awake, and leaves oMLX's settings exactly as it found them.

## Setup (once)

    uv sync                  # fetches Python 3.12 + pytest, creates uv.lock (commit it)
    cp .env.example .env     # then put the MAIN oMLX API key in .env
    uv run omlx-bench models # check the connection; copy model ids from here

Sub-keys don't work: oMLX's admin routes accept only the main key.
Context benchmarks need **Memory Guard** on in oMLX.

## Everyday use

    # 1. edit plan.toml: models, variants, which tests
    uv run omlx-bench plan                 # adds new steps to queue.csv (safe to re-run)
    uv run omlx-bench run                  # works through the queue, model by model
    uv run omlx-bench status               # progress + failures

    uv run omlx-bench run --limit 1        # just the next step (smoke test)
    uv run omlx-bench run --model X --suite intelligence
    uv run omlx-bench retry S0007          # a step that used up its 3 attempts
    uv run omlx-bench retry --all-failed

Ctrl-C at any point cancels the running test on oMLX, restores settings, and leaves
the step pending for next time. Failed steps retry automatically on the next `run`.

## Settings

    uv run omlx-bench settings show MODEL          # or no MODEL for global settings
    uv run omlx-bench settings set MODEL temperature=0.2 enable_thinking=false
    uv run omlx-bench settings global memory_guard_tier=balanced
    uv run omlx-bench settings restore SNAPSHOT    # undo a manual change (name is printed)
    uv run omlx-bench settings restore             # finish any restore a crash left open

Benchmarks never leave a change behind: settings are snapshotted before each step
and restored after it. Manual `settings set` changes are kept. Host, port, keys and
model folders are off limits. Every change is logged in `settings/changes.log`.

## Files

| File | What |
|---|---|
| `plan.toml` | What to run (you edit this) |
| `queue.csv` | Every step: status (pending/running/pass/fail), attempts, last error |
| `results/index.csv` | Every result: exact params, settings, headline numbers, omlx.ai link, detail paths |
| `results/details/*.json` | Full record per attempt, including every streamed event (`*-FAILED.json` for failures) |
| `results/exports/<model>_<suite>.json` | Intelligence results in oMLX's own export format, ready for the dashboard |
| `settings/snapshots/`, `settings/changes.log` | Settings backups and the change log |
| `docs/adr/` | Why it works this way |

## Keeping the Mac awake

If Amphetamine is running, `run` starts a session and ends it afterwards. If you
already have a session going, it leaves yours alone. The first time, macOS asks whether
Terminal may control Amphetamine; allow it. Without Amphetamine it uses `caffeinate`,
which can't keep a closed laptop awake on battery. `--no-keep-awake` turns this off.

## Development

    uv run pytest            # 58 tests against a fake oMLX server; no real server needed
