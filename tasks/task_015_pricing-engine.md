# Task 015 — Deterministic pricing engine

Phase 2 · Depends on: 012, 013, 014 · Requirements: PRC-1, PRC-2, PRC-3, PRC-4

## Read first
- AGENTS.md
- docs/06_PRICING_ENGINE.md
- docs/09_CODING_STANDARDS.md
- docs/11_BUSINESS_RULES_INTERVIEW.md

## Needs from owner
- Margin rules and the accountant-approved tax table (or clearly marked placeholders).

## Scope
- Implement docs/06 exactly: floor/target/ceiling/recommended, statuses (OK, BELOW_FLOOR, UNCOMPETITIVE), explanations, rounding, FX snapshots.
- Rule loader from `config/business_rules.yaml` with versioning; tax tables loaded separately.
- Immutable `price_calculations` records.
- Property tests + >= 30 golden scenarios.

## Acceptance criteria
- All properties in docs/06 hold; golden tests pass.
- No float usage and no LLM calls in the module (static tests).
- Placeholders in tax/margin rules cause `NEEDS_CONFIG`, not a silent default.

## Human gates / approvals
- BELOW_FLOOR and UNCOMPETITIVE route to the owner (LOCKED).

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-015: ...`, write the report from AGENTS.md, then STOP.
