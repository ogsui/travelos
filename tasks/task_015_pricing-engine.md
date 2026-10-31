# Task 015 — Deterministic pricing engine

Phase 2 · Depends on: 012, 013, 014 · Requirements: PRC-1, PRC-2, PRC-3, PRC-4

## Read first
- AGENTS.md
- docs/06_PRICING_ENGINE.md
- docs/09_CODING_STANDARDS.md
- docs/11_BUSINESS_RULES_INTERVIEW.md

## Needs from owner
- Margin rules and the accountant-approved tax table (or clearly marked placeholders).

## Split (015a-015d)
The umbrella task is too large for one review and depends on owner/accountant money
values, so it is delivered in four slices:

| Slice | Scope | State |
|---|---|---|
| 015a | Rule schema, YAML loader, version hash and placeholder detection (`NEEDS_CONFIG`) | done; production file ships all-placeholder |
| 015b1 | Exact FX conversion, rounding, margin bands and tax-table resolution | done; synthetic rates/rates only |
| 015b2 | Quote assembly (floor/target/ceiling/recommended), statuses, explanations, property + scenario tests | blocked on owner margins, tax table/version, gateway policy, forex buffer, rounding, quote validity and 30 golden scenarios |
| 015c | Persistence: `pricing_rule_sets`, `tax_rule_tables`, `fx_rates`, immutable `price_calculations` | not started |
| 015d | Trusted competitor ceiling and BELOW_FLOOR/UNCOMPETITIVE approval routing | not started |

015a deliberately produces no price. `config/business_rules.yaml` contains only
placeholders, so every load reports `NEEDS_CONFIG` and the engine must refuse to quote.
015b1 computes no customer price either: it supplies the exact primitives the
assembly step and its tests use.

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
