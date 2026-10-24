# Task 020 — Booking orchestration

Phase 3 · Depends on: 005, 012, 019 · Requirements: BKG-1, BKG-2, BKG-3, BKG-4, BKG-5

## Read first
- AGENTS.md
- docs/02_ARCHITECTURE.md
- docs/05_APPROVAL_MATRIX.md
- docs/06_PRICING_ENGINE.md
- docs/07_SECURITY_COMPLIANCE.md

## Needs from owner
- Supplier hold/ticketing rules from account managers.

## Scope
- State machine per docs/02; fare revalidation with tolerance; passenger-data validation (names as per passport, age vs fare type).
- Idempotent booking calls, hold/ticketing deadlines, partial-failure handling with human task, booking references/tickets/vouchers stored.
- Human final confirmation step; supplier payment/wallet funding recorded by humans (`supplier_payments`).

## Acceptance criteria
- Worker crash mid-booking does not double-book (test).
- Price moved beyond tolerance -> blocked and escalated.
- Cannot reach TICKETING without a recorded human confirmation (LOCKED test).

## Human gates / approvals
- Booking confirmation is LOCKED at A1; supplier funding A0.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-020: ...`, write the report from AGENTS.md, then STOP.
