# 4. Standard library only at runtime; uv manages Python and tooling

Date: 2026-11-09 · Status: Accepted

## Context
The scaffold planned httpx, typer and rich. Two facts argue against them:
- The tool talks to one localhost server with plain JSON and Server-Sent Events.
  `urllib` + reading the response line by line handles both.
- Development and testing happen in sandboxes with no package index access, so any
  third-party dependency can't be tested there before it reaches Astrid's Mac.
Astrid also wants her tooling local and fully owned; zero dependencies is the
smallest thing to own.

## Decision
Runtime uses only the Python standard library (urllib, json, csv, tomllib, argparse,
subprocess). Python ≥ 3.11 for `tomllib`; `.python-version` pins 3.12.
uv still does everything around it: fetching Python, the venv, `uv run omlx-bench`,
and the dev group (pytest).

## Consequences
- `uv sync` installs nothing but the package itself; nothing to audit or update.
- Every line can be tested before it ships.
- Terminal output is plain text (no rich progress bars). If that hurts, add rich
  later as an optional extra, recorded in a new ADR.
