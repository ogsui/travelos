# Task 012 — Supplier adapters & price snapshots

Phase 2 · Depends on: 004, 007 · Requirements: SUP-1, SUP-2, SUP-3, SUP-4

## Read first
- AGENTS.md
- docs/04_INTEGRATIONS.md
- docs/03_DATABASE.md
- docs/02_ARCHITECTURE.md

## Needs from owner
- TBO and Riya API docs + sandbox credentials (answers to docs/04 questions).

## Scope
- Split into sub-tasks: 12a interface + models + SupplierSimulator + snapshot store + circuit breaker + contract tests; 12b TBO adapter; 12c Riya adapter; 12d optional PortalSessionAdapter.
- Normalized `Offer`, `FareRules`, `CancellationRules` models; search, price, rules, hold, book, cancel, retrieve, wallet-read.
- Simulator supports price drift, sold-out, timeouts, partial failures, hold expiry.
- Immutable snapshots with TTL and request hash; parallel searches with per-supplier timeouts.
- 12b/12c only from supplied official docs; otherwise stubs with `TODO(owner)` questions. 12d is OFF by default, rate-limited, never bypasses OTP/CAPTCHA, requires a recorded risk acceptance.

## Acceptance criteria
- Same contract suite passes for the simulator and (when available) sandbox adapters.
- Snapshots cannot be modified.
- One supplier failing returns partial results with a flag (test).

## Human gates / approvals
- Booking/cancel calls exist but are only reachable through the booking orchestrator (task 20) and approval engine.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-012: ...`, write the report from AGENTS.md, then STOP.
