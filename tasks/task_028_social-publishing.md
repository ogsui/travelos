# Task 028 — Social publishing adapters

Phase 4 · Depends on: 027, 005 · Requirements: MKTG-3, MKTG-4

## Read first
- AGENTS.md
- docs/04_INTEGRATIONS.md
- docs/05_APPROVAL_MATRIX.md
- docs/07_SECURITY_COMPLIANCE.md

## Needs from owner
- Meta/LinkedIn/YouTube/X developer access and page ownership.

## Scope
- PublishAdapter + Mock; real adapters for Instagram/Facebook (Graph API), LinkedIn Pages, YouTube Shorts, Threads, X where access exists.
- Scheduling (heuristic best-times first, data-driven later), token refresh, metrics ingestion, comment/DM triage with drafted replies.

## Acceptance criteria
- Mock publish tested end to end.
- Token expiry handled.
- Kill switch per account honoured.

## Human gates / approvals
- Posts A1; replies A1->A2 for simple thanks/FAQ.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-028: ...`, write the report from AGENTS.md, then STOP.
