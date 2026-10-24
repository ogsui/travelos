# Task 023 — Support agent & disruption handling

Phase 3 · Depends on: 006, 009, 020, 005 · Requirements: SUPT-1, SUPT-2, SUPT-3, SUPT-4

## Read first
- AGENTS.md
- docs/05_APPROVAL_MATRIX.md
- docs/07_SECURITY_COMPLIANCE.md

## Needs from owner
- Approved FAQ/KB content and escalation contacts.

## Scope
- KB model with approved articles; intents (status, baggage, check-in, documents, change, cancel, refund, visa).
- Answers grounded in booking data/KB; otherwise 'let me confirm with our team'.
- Change/cancel/refund requests -> workflows with computed charges for human execution.
- Disruption detection hooks and playbook with urgent paging; complaint/sentiment detection.

## Acceptance criteria
- No answer without a grounding source (eval).
- Refund/cancel never executes automatically (LOCKED test).
- Urgent disruption pages staff in test.

## Human gates / approvals
- Support replies A1 -> A2 per matrix; complaints human-led.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-023: ...`, write the report from AGENTS.md, then STOP.
