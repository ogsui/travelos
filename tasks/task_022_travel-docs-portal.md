# Task 022 — Travel documents & customer portal

Phase 3 · Depends on: 020, 021 · Requirements: DOC-1, DOC-2, DOC-3, DOC-4

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md
- docs/07_SECURITY_COMPLIANCE.md

## Needs from owner
- Weather/FX provider choices; visa source decision.

## Scope
- Itinerary PDF, ticket/voucher bundle, checklist, emergency contacts, weather/currency info.
- Automatic consistency check (names, dates, PNRs, amounts) before sending.
- Magic-link customer portal; delivery via WhatsApp/email; visa info only from the configured source, staff-verified.

## Acceptance criteria
- Mismatched document blocked (test).
- Portal links expire and are revocable.
- Visa section shows 'to be confirmed' without a verified source.

## Human gates / approvals
- Send documents A2 with hold window; visa guidance A1.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-022: ...`, write the report from AGENTS.md, then STOP.
