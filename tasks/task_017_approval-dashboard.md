# Task 017 — Approval & inbox dashboard (mobile-first)

Phase 2 · Depends on: 003, 005, 016 · Requirements: APR-2, APR-3

## Read first
- AGENTS.md
- docs/05_APPROVAL_MATRIX.md
- docs/09_CODING_STANDARDS.md

## Needs from owner
- Nothing.

## Scope
- Next.js dashboard: approval queue, review screen (original message vs extraction, uncertain fields highlighted, price breakdown with cost/margin/taxes, fare rules, revision diff), edit-in-place, approve/reject with reason, ask-customer action.
- Inbox/conversation view with drafts; human task list; kill-switch panel (owner).
- Role-aware UI; server-side authorization; works on phone.

## Acceptance criteria
- Playwright e2e: approve, edit, reject, escalate.
- Unauthorized roles cannot see/act (tests).
- Lighthouse accessibility >= 90 on core screens.

## Human gates / approvals
- This is the approval surface.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-017: ...`, write the report from AGENTS.md, then STOP.
