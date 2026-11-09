# omlx-bench

Runs oMLX performance, intelligence and context benchmarks one test at a time
through the admin API, tracking every step and result in plain files.

    uv sync
    cp .env.example .env   # then put your MAIN oMLX API key in .env
    uv run omlx-bench models
    uv run omlx-bench plan     # plan.toml -> queue.csv
    uv run omlx-bench run      # works through queue.csv
    uv run omlx-bench status

Files: `plan.toml` (what to run) · `queue.csv` (every step + pass/fail) ·
`results/index.csv` (every result + params + where details live) ·
`results/details/*.json` (full detail per result). See PLAN.md.
