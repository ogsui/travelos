# Task 040 — Pilot: enable live mode safely

Phase 7 · Depends on: 039 · Requirements: CORE-1

## Read first
- AGENTS.md
- docs/08_TESTING_EVAL.md
- docs/05_APPROVAL_MATRIX.md
- docs/07_SECURITY_COMPLIANCE.md

## Needs from owner
- Go-live checklist signed (docs/08).

## Scope
- Live-mode enablement checklist and startup guards; staged channel enablement; rollback plan; staff training guide; first-two-weeks monitoring dashboard.

## Acceptance criteria
- Live refuses to start with any checklist item missing.
- Rollback rehearsed.
- All LOCKED actions verified in staging.

## Human gates / approvals
- All actions start at A1 per matrix.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-040: ...`, write the report from AGENTS.md, then STOP.
