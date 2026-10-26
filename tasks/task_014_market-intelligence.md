# Task 014 — Competitor price adapters

Phase 2 · Depends on: 007, 012 · Requirements: MKT-1, MKT-2, MKT-3, MKT-4

## Read first
- AGENTS.md
- docs/04_INTEGRATIONS.md
- docs/06_PRICING_ENGINE.md
- docs/07_SECURITY_COMPLIANCE.md

## Needs from owner
- Per-portal authorization decision (account owner, permitted workflows, terms review) recorded in `docs/DECISIONS.md`.

## Scope
- `CompetitorPriceAdapter`; ManualEntry (dashboard hook), LicensedData stub, and authorized logged-in page collection (Playwright, isolated worker) for sites the owner enables.
- No API for competitor-price collection. Each portal flag defaults OFF and requires owner review of account authorization and terms; throttle and cache. CAPTCHA or OTP/MFA pauses the run for an authorized operator to complete interactively when provider terms permit; resume requires explicit operator action and verified restored access. Access-denied and anti-bot blocks become `unavailable` pending owner resolution. Never automate challenge solving or evade access controls.
- Comparability scoring (route, dates, pax, fare class, baggage, refundability) and outlier handling.
- Parsers tested on saved HTML fixtures; seasonality/demand signals from the agency's own history.

## Acceptance criteria
- Portal failure never blocks quoting (test).
- Rate limit and cache tests.
- Comparability unit tests.
- No challenge-solving or access-control-evasion code present (review check).

## Human gates / approvals
- Enabling a site is owner-only.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-014: ...`, write the report from AGENTS.md, then STOP.
