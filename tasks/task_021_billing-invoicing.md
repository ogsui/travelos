# Task 021 — Billing, invoices, ledger

Phase 3 · Depends on: 019, 020 · Requirements: BIL-1, BIL-2, BIL-3

## Read first
- AGENTS.md
- docs/06_PRICING_ENGINE.md
- docs/07_SECURITY_COMPLIANCE.md
- docs/11_BUSINESS_RULES_INTERVIEW.md

## Needs from owner
- Accountant-approved invoice formats, series rules, tax tables.

## Scope
- Proforma/tax invoices, receipts, credit notes, vouchers; gapless numbering per series and financial year; void + credit note flow.
- Tax from approved tables only; ledger entries per booking (receipts, supplier cost, margin, taxes).
- PDF rendering; accountant approval workflow.

## Acceptance criteria
- Numbering is gapless under concurrency (test).
- Issued invoices are immutable.
- Invoice totals equal quote/price calculation (test).

## Human gates / approvals
- Accountant approves per matrix; filing is A0 (export only).

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-021: ...`, write the report from AGENTS.md, then STOP.
