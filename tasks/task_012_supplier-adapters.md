# Task 012 — Supplier adapters & price snapshots

Phase 2 · Depends on: 004, 007 · Requirements: SUP-1, SUP-2, SUP-3, SUP-4

## Read first
- AGENTS.md
- docs/04_INTEGRATIONS.md
- docs/03_DATABASE.md
- docs/02_ARCHITECTURE.md

## Needs from owner
- Authorized supplier-portal accounts, the permitted page workflows, and confirmation that automated price/inventory collection is allowed under the account terms. Do not request or store credentials in task files.

## Scope
- Split into sub-tasks: 12a interface + models + SupplierSimulator + snapshot store + circuit breaker + contract tests; 12b/12c authorized supplier-portal price and inventory adapters when portal access and terms are supplied; 12d optional PortalSessionAdapter for other explicitly authorized portal workflows.
- Normalized `Offer`, `FareRules`, `CancellationRules` models; search, price, rules, hold, book, cancel, retrieve, wallet-read.
- Simulator supports price drift, sold-out, timeouts, partial failures, hold expiry.
- Immutable snapshots with TTL and request hash; parallel searches with per-supplier timeouts.
- Price/inventory collection uses authorized supplier pages, not APIs. Portal account details, permitted workflows, and terms/access authorization must be supplied by the owner; otherwise leave a stub with `TODO(owner)` questions. Portal adapters are OFF by default and rate-limited. On CAPTCHA or OTP/MFA, pause and notify an authorized operator; if provider terms permit, the operator may complete the challenge interactively in the authorized session. Resume only after explicit operator action and verification that access is restored. Access-denied and anti-bot blocks become `unavailable` and require owner resolution. Never automate challenge solving or evade access controls. Booking/cancel actions remain separate and approval-gated.

## Acceptance criteria
- Same contract suite passes for the simulator and (when available) sandbox adapters.
- Snapshots cannot be modified.
- One supplier failing returns partial results with a flag (test).

## Human gates / approvals
- Booking/cancel calls exist but are only reachable through the booking orchestrator (task 20) and approval engine.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-012: ...`, write the report from AGENTS.md, then STOP.
