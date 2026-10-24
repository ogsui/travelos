# Task 007 — Synthetic data generators

Phase 1 · Depends on: 002 · Requirements: CRM-3

## Read first
- AGENTS.md
- docs/08_TESTING_EVAL.md
- docs/03_DATABASE.md

## Needs from owner
- Nothing.

## Scope
- Deterministic generators (seeded) for every dataset in docs/08: customers with consent states, 150+ inquiries with gold requirements, 40 gold quotes, supplier simulator inventory/price drift, competitor snapshots, channel webhook fixtures, payments, marketing history, adversarial set.
- `make seed` loads the dataset; `data/README.md` documents schemas.
- Include Hinglish/Hindi samples and family-travel edge cases (infants, child ages, name spelling).

## Acceptance criteria
- Same seed -> identical data.
- Test asserts all PII-like fields use reserved fake patterns (fake domains, non-routable numbers).
- Gold files validate against schemas.

## Human gates / approvals
- None.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-007: ...`, write the report from AGENTS.md, then STOP.
