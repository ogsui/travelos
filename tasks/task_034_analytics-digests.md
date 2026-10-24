# Task 034 — Digests, alerts, recommendations

Phase 5 · Depends on: 033, 006 · Requirements: AN-2, AN-3

## Read first
- AGENTS.md
- docs/05_APPROVAL_MATRIX.md
- docs/07_SECURITY_COMPLIANCE.md

## Needs from owner
- Nothing.

## Scope
- Weekly owner digest and anomaly alerts via WhatsApp/email using verified numbers only (no free-form LLM figures).
- Recommendations (e.g. shift ad budget, push destination) as approval proposals.

## Acceptance criteria
- Every number in a digest traces to a query (test).
- Recommendations never auto-apply.

## Human gates / approvals
- Apply recommendation A1.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-034: ...`, write the report from AGENTS.md, then STOP.
