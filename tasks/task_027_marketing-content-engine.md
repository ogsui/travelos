# Task 027 — Content calendar & generation

Phase 4 · Depends on: 005, 006, 012 · Requirements: MKTG-1, MKTG-2, MKTG-5

## Read first
- AGENTS.md
- docs/05_APPROVAL_MATRIX.md
- docs/07_SECURITY_COMPLIANCE.md
- docs/04_INTEGRATIONS.md

## Needs from owner
- Brand voice samples, photo/video library with licences.

## Scope
- Idea engine from real offers, festivals, seasonality; calendar.
- Caption/hashtag/blog drafts; image selection (own/licensed first; AI imagery labelled); short-video renderer from templates.
- Price/offer accuracy checker against unexpired snapshots; brand voice linting; asset licence tracking.

## Acceptance criteria
- Content with an unverifiable price is blocked (test).
- Assets carry source/licence metadata.
- Drafts enter approval workflow.

## Human gates / approvals
- Evergreen A1->A2; price/offer posts A1.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-027: ...`, write the report from AGENTS.md, then STOP.
