# 2. One test per step, run model by model

Date: 2026-11-09 · Status: Accepted

## Context
oMLX's endpoints accept several tests per call (many prompt lengths, batch sizes or
suites at once). A failure mid-call loses all of them, and a single call can run for
hours. The server also allows only one benchmark of any kind at a time (409 otherwise).

## Decision
A **step** is the smallest unit oMLX will run:
- performance: one prompt length, or one batch size (oMLX requires a prompt length on
  every run and batch tests always use pp1024, so a batch step is pp1024 + one batch size;
  that pp1024 row is recorded and flagged `duplicate_of_single`);
- intelligence: one suite;
- context: one target size.

Steps run strictly in sequence, **model by model**: every step of one model
(performance → intelligence → context) before the next model.

## Consequences
- A failure costs one test; retries are cheap and targeted.
- oMLX loads and unloads the model around every step. That load time is the price of
  isolation, accepted deliberately.
- Each step uploads to omlx.ai separately (per prompt length / per suite), matching
  what the dashboard already does.
