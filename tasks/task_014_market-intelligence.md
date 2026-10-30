# Task 014 — Competitor price adapters

Phase 2 · Depends on: 007, 012 · Requirements: MKT-1, MKT-2, MKT-3, MKT-4

## Read first
- AGENTS.md
- docs/04_INTEGRATIONS.md
- docs/06_PRICING_ENGINE.md
- docs/07_SECURITY_COMPLIANCE.md

## Needs from owner
- Per-portal authorization decision (account owner, permitted workflows, terms review) recorded in `docs/DECISIONS.md`.
- Confirmation of the comparability defaults (threshold, maximum age, median-vs-lowest reference) before the price ceiling depends on them; tracked in `docs/BACKLOG.md`.

## Scope
- `CompetitorPriceAdapter`; ManualEntry (dashboard hook), LicensedData stub, and authorized logged-in page collection (Playwright, isolated worker) for sites the owner enables.
- No API for competitor-price collection. Each portal flag defaults OFF and requires owner review of account authorization and terms; throttle and cache. CAPTCHA or OTP/MFA pauses the run for an authorized operator to complete interactively when provider terms permit; resume requires explicit operator action and verified restored access. Access-denied and anti-bot blocks become `unavailable` pending owner resolution. Never automate challenge solving or evade access controls.
- Comparability scoring (route, dates, pax, fare class, baggage, refundability) and outlier handling.
- Parsers tested on saved HTML fixtures; seasonality/demand signals from the agency's own history.

## Split (agreed 2026-10-27)
- 014a1 (delivered): pure `market` comparability domain. `ComparabilityBasis` / `CompetitorPrice` / `ComparabilityAssessment`, six scored attributes, identity refusals, unknown-detail cap, median reference with MAD outlier filtering, 44 unit tests including two hypothesis properties. No persistence, adapter or portal code.
- 014a2a (delivered): `CompetitorPriceAdapter` interface, `ManualEntryAdapter` (dashboard hook, validated entries), `LicensedDataAdapter` stub, `fetch_competitor_prices` / `competitor_observations` with per-adapter failure isolation and exception-type-only details. 18 unit tests. No persistence, flags, cache or portal code.
- 014a2b1 (delivered): `competitor_snapshots` (migration 0016), append-only `CompetitorSnapshotStore.record/latest`, canonical `query_hash`, per-observation comparability score, blocked/unavailable runs stored explicitly, audit entry per run. 18 unit + 17 integration tests. No collection loop, flags or throttle.
- 014a2b2 (remaining): per-site feature flags OFF by default, cache/throttle helpers and the collection loop that composes adapters, store and flags.
- 014b (blocked): authorized logged-in page collection (Playwright, isolated worker), fixture parsers and the operator pause/resume flow. Needs the owner's per-portal authorization and terms review in `docs/DECISIONS.md`.
- MKT-4 seasonality/demand signals from the agency's own history are not started; they belong to a later slice once quoting history exists.

## Acceptance criteria
- Portal failure never blocks quoting (test). Done for the scoring path in 014a1; a failing adapter is covered in 014a2a; the portal path is 014b.
- Rate limit and cache tests. 014a2b2/014b.
- Comparability unit tests. Done (014a1).
- Adapter failure isolation and no-leak tests. Done (014a2a).
- Snapshot persistence, tenant/query isolation and append-only tests. Done (014a2b1).
- No challenge-solving or access-control-evasion code present (review check). Holds: no portal code exists yet.

## Human gates / approvals
- Enabling a site is owner-only.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-014: ...`, write the report from AGENTS.md, then STOP.
