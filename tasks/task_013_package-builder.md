# Task 013 — Itinerary & package builder

Phase 2 · Depends on: 010, 012 · Requirements: PKG-1, PKG-2, PKG-3

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md
- docs/02_ARCHITECTURE.md

## Needs from owner
- Nothing.

## Scope
- Itinerary model (days, segments, components) for family, corporate, group, individual trips.
- Occupancy/age rules (infants, child ages, room types), multi-city routing, supplier deadlines.
- Three explainable option tiers via deterministic ranking; LLM only for narrative text.
- Compare output to gold quotes.

## Split (agreed 2026-10-27)
- 013a (delivered): pure `packages` models, occupancy/age rules, deterministic assembly, multi-city routing, supplier deadlines, explanations, unit tests.
- 013b1 (delivered): persistence (`itineraries`, `itinerary_items`, migration 0015), snapshot links, hand-checked `data/eval/package_gold.jsonl` and the recorded >=85% component baseline.
- 013b2 (remaining): value/standard/premium option tiers via deterministic ranking. Needs an owner rule for what makes a package premium; normalized `Offer` has no star/board/cabin/direct attribute.

## Acceptance criteria
- Component match >= 85% vs gold (record baseline). Recorded: 1.0 across 11 hand-checked cases in `data/eval/results/package_components_v1.json`.
- Invalid occupancy rejected with reasons. Done (task 013a).
- Explanations list why each item was chosen. Done (task 013a).

## Human gates / approvals
- None (internal).

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-013: ...`, write the report from AGENTS.md, then STOP.
