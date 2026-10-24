# Task 025 — CRM intelligence & segments

Phase 4 · Depends on: 008, 021 · Requirements: CRM-5

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md
- docs/03_DATABASE.md

## Needs from owner
- Nothing.

## Scope
- LTV, RFM, lead scoring, segments; preferences derived only from booked/stated facts.
- Reactivation lists (e.g. no booking in 12+ months) with consent filters.

## Acceptance criteria
- Scores reproducible and explainable.
- Segments never include non-consented contacts for marketing audiences (test).

## Human gates / approvals
- None (internal).

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-025: ...`, write the report from AGENTS.md, then STOP.
