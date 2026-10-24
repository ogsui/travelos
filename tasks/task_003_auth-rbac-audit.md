# Task 003 — Auth, RBAC, audit service, kill switches

Phase 1 · Depends on: 002 · Requirements: CORE-1, CORE-2, CORE-3

## Read first
- AGENTS.md
- docs/07_SECURITY_COMPLIANCE.md
- docs/05_APPROVAL_MATRIX.md

## Needs from owner
- Nothing.

## Scope
- Email+password login (argon2), sessions/JWT, TOTP hook for owner/admin.
- Roles owner/agent/accountant/admin and a permission decorator used by every endpoint.
- Audit writer service used by all state changes; helper to record before/after diffs.
- Feature-flag service (cached) and owner-only kill-switch endpoints; all flags default OFF.

## Acceptance criteria
- Every route has a role test (allowed + denied).
- Login, permission denials and flag changes are audited.
- Flag cache invalidation tested.

## Human gates / approvals
- Changing a kill switch or autonomy level is owner-only and audited.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-003: ...`, write the report from AGENTS.md, then STOP.
