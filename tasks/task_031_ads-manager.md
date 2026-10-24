# Task 031 — Paid ads manager

Phase 4 · Depends on: 027, 005 · Requirements: ADS-1, ADS-2, ADS-3

## Read first
- AGENTS.md
- docs/04_INTEGRATIONS.md
- docs/05_APPROVAL_MATRIX.md
- docs/07_SECURITY_COMPLIANCE.md

## Needs from owner
- Google Ads/Meta Ads accounts, billing caps, developer tokens.

## Scope
- AdsAdapter + Mock; Google Ads and Meta Ads real adapters.
- Campaign drafts, creatives, copy, A/B tests; pacing; offline conversion upload from real bookings.
- Hard spend caps enforced in code; anomaly detection with autonomous PAUSE only.

## Acceptance criteria
- Spend can never exceed caps even if the model proposes it (test).
- Pause works in mock; budget increase needs owner approval.
- Conversion attribution reproducible.

## Human gates / approvals
- Create/edit A1; budget increases LOCKED owner.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-031: ...`, write the report from AGENTS.md, then STOP.
