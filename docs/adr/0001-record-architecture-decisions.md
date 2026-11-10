# 1. Record architecture decisions

Date: 2026-11-09 · Status: Accepted

## Context
This tool drives benchmarks that take hours and touch a shared server's settings.
The reasons behind its shape (why one test per step, why settings are always
restored) need to survive past the chat they were decided in, both for Astrid and
for whoever (human or model) changes the code later.

## Decision
Keep ADRs in `docs/adr/`, numbered, in Nygard format. Write the ADR in its own
commit *before* the code it governs, so `git log` reads as decision → implementation.
PLAN.md stays the living plan; ADRs are the fixed record.

## Consequences
- Each architectural change costs a short document.
- Superseded decisions remain readable, with a pointer to their replacement.
