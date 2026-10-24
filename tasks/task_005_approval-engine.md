# Task 005 — Approval engine & autonomy policies

Phase 1 · Depends on: 003, 004 · Requirements: APR-1, APR-2, APR-3, APR-4

## Read first
- AGENTS.md
- docs/05_APPROVAL_MATRIX.md
- docs/03_DATABASE.md

## Needs from owner
- Nothing.

## Scope
- Seed `autonomy_policies` from docs/05 including LOCKED rows.
- ActionPolicy resolver: given action type, value, context -> auto | needs_approval(level) | deny.
- ApprovalRequest lifecycle: pending/approved/edited/rejected/expired with proposed-vs-final diff, reviewer, reason.
- Expiry + escalation timers; four-eyes above threshold; auto-demotion hook driven by `autonomy_metrics`.
- API + events so any module can request approval without knowing UI details.

## Acceptance criteria
- LOCKED actions cannot be set to auto by config, API or DB seed (test).
- Every decision audited with before/after.
- Demotion triggers on incident/approval-rate thresholds (test).
- Expired requests escalate and notify via human_tasks.

## Human gates / approvals
- This task implements the gates; changing policies is owner-only.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-005: ...`, write the report from AGENTS.md, then STOP.
