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

## Slices
Owner-approved 5-slice split (one commit each, full verification each time).

| Slice | Scope | State |
|---|---|---|
| 017a | Server-side cookie auth + MFA sign-in, server-only API client, role-aware mobile-first shell, ESLint/Vitest/Playwright harness, `dashboard` CI job | delivered |
| 017b | Human-tasks read API + approval queue screen | next |
| 017c | Approval subject read model (original message vs extraction, price breakdown, fare rules, revision diff) + review/decide screen | pending |
| 017d | Conversation/draft read API + inbox view | pending |
| 017e | Owner kill-switch panel, accessibility/Lighthouse pass, full approve/edit/reject/escalate e2e | pending |

## Acceptance criteria
- Playwright e2e: approve, edit, reject, escalate.
- Unauthorized roles cannot see/act (tests).
- Lighthouse accessibility >= 90 on core screens.

## Human gates / approvals
- This is the approval surface.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-017: ...`, write the report from AGENTS.md, then STOP.
