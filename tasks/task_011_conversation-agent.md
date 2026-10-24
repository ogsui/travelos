# Task 011 — Sales conversation agent

Phase 2 · Depends on: 005, 006, 010 · Requirements: SALES-1, SALES-2, SALES-3, SALES-4, SALES-5

## Read first
- AGENTS.md
- docs/05_APPROVAL_MATRIX.md
- docs/11_BUSINESS_RULES_INTERVIEW.md
- docs/07_SECURITY_COMPLIANCE.md

## Needs from owner
- Brand voice samples, objection playbook, FAQ list, discount rules (from the interview).

## Scope
- Acknowledgement templates; clarifying-question and recommendation drafts using business rules.
- Objection handling and upsell suggestions (transfers, tours, upgrades; insurance only through a compliant flow or staff).
- Handoff triggers (anger, complaint, large value, legal/visa/refund, loops) -> human task with conversation summary.
- Replies never state price/availability/policy unless sourced from a quote, snapshot or approved KB.
- Eval rubric: helpful, accurate, on-brand, no unsupported promises.

## Acceptance criteria
- Unsupported-promise rate 0 in eval.
- Handoff triggers tested.
- Injection tests pass.
- All drafts go through approval per matrix.

## Human gates / approvals
- Free-form replies A1; discounts follow rules, below floor -> owner.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-011: ...`, write the report from AGENTS.md, then STOP.
