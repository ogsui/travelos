# Task 014 — Competitor price adapters

Phase 2 · Depends on: 007, 012 · Requirements: MKT-1, MKT-2, MKT-3, MKT-4

## Read first
- AGENTS.md
- docs/04_INTEGRATIONS.md
- docs/06_PRICING_ENGINE.md
- docs/07_SECURITY_COMPLIANCE.md

## Needs from owner
- Owner decision per site to enable scraping (recorded in docs/DECISIONS.md).

## Scope
- `CompetitorPriceAdapter`; ManualEntry (API + dashboard hook), LicensedData stub, PublicPageScraper (Playwright, isolated worker) for MakeMyTrip, Goibibo, Booking.com, Agoda, EaseMyTrip, Yatra.
- Per-site flag default OFF, throttling, caching, honest identification, no login, no evasion; blocked/missing -> `unavailable`.
- Comparability scoring (route, dates, pax, fare class, baggage, refundability) and outlier handling.
- Parsers tested on saved HTML fixtures; seasonality/demand signals from the agency's own history.

## Acceptance criteria
- Scraper failure never blocks quoting (test).
- Rate limit and cache tests.
- Comparability unit tests.
- No evasion code present (review check).

## Human gates / approvals
- Enabling a site is owner-only.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-014: ...`, write the report from AGENTS.md, then STOP.
