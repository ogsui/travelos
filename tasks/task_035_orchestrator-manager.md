# Task 035 — Manager: SLAs, routing, graduation

Phase 5 · Depends on: 004, 005, 033 · Requirements: ORC-1, ORC-2

## Read first
- AGENTS.md
- docs/02_ARCHITECTURE.md
- docs/05_APPROVAL_MATRIX.md

## Needs from owner
- Nothing.

## Scope
- Priority queue and SLA timers; stuck-workflow detector; DLQ review; human task routing and daily digest; urgent paging.
- Autonomy graduation tracker producing promote/demote proposals for the owner.

## Acceptance criteria
- Stuck workflows surface within a configured time (test).
- Graduation proposals follow docs/05 rules.
- Paging works via mock.

## Human gates / approvals
- Policy changes owner-only.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-035: ...`, write the report from AGENTS.md, then STOP.
