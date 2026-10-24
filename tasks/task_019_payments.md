# Task 019 — Payments & reminders

Phase 3 · Depends on: 004, 005, 016 · Requirements: PAY-1, PAY-2, PAY-3, PAY-4

## Read first
- AGENTS.md
- docs/04_INTEGRATIONS.md
- docs/07_SECURITY_COMPLIANCE.md
- docs/05_APPROVAL_MATRIX.md

## Needs from owner
- Payment gateway choice + sandbox keys.

## Scope
- `PaymentAdapter` with MockGateway and one real gateway (Razorpay/Cashfree/PayU) via hosted payment links.
- Payment schedules/instalments; signed webhooks; idempotent recording; daily reconciliation job.
- Reminder cadence from approved templates; refund requests recorded and later marked executed by a human.

## Acceptance criteria
- Duplicate/late/out-of-order webhooks handled (tests).
- No card data stored (test scans schema).
- Reconciliation flags mismatches as human tasks.

## Human gates / approvals
- Create link A1; the system never moves money.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-019: ...`, write the report from AGENTS.md, then STOP.
