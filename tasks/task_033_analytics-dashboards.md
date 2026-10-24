# Task 033 — Analytics marts & dashboards

Phase 5 · Depends on: 021, 026, 031 · Requirements: AN-1

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md
- docs/03_DATABASE.md

## Needs from owner
- Nothing.

## Scope
- Marts/materialized views from docs/03; dashboard pages for revenue, profit/margin, conversion by stage, lead sources, destinations, campaign ROI, CAC, repeat rate, agent performance, outstanding payments, response times.

## Acceptance criteria
- Dashboard totals equal ledger totals (test).
- Role-based visibility (margins hidden from roles that shouldn't see them).

## Human gates / approvals
- None.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-033: ...`, write the report from AGENTS.md, then STOP.
