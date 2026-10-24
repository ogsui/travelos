# Task 018 — Eval harness & shadow comparator

Phase 2 · Depends on: 010, 013, 015, 016 · Requirements: APR-4

## Read first
- AGENTS.md
- docs/08_TESTING_EVAL.md

## Needs from owner
- Nothing.

## Scope
- `make eval` runner for parser, packages, pricing, replies, content accuracy, injection, latency/cost; stored results per prompt version; CI regression gate.
- Shadow comparator: import staff quotes (CSV/Excel), diff vs system quotes (price, components, time taken), weekly report generator.
- End-to-end 'replay an inquiry' CLI: inquiry -> quote PDF on synthetic data (this is the owner DEMO).

## Acceptance criteria
- Eval report prints the gate table from docs/08.
- Replay of 20 synthetic inquiries completes end to end.
- Comparator output reviewed by a human.

## Human gates / approvals
- Nothing is sent; demo runs in `test` mode.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-018: ...`, write the report from AGENTS.md, then STOP.
