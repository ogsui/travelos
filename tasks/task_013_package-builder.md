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

## Acceptance criteria
- Component match >= 85% vs gold (record baseline).
- Invalid occupancy rejected with reasons.
- Explanations list why each item was chosen.

## Human gates / approvals
- None (internal).

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-013: ...`, write the report from AGENTS.md, then STOP.
