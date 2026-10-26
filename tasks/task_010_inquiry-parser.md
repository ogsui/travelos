# Task 010 — Inquiry parser & missing-info loop

Phase 2 · Depends on: 006, 007, 008, 009 · Requirements: INQ-1, INQ-2, INQ-3, INQ-4

Slices: 010a (schema + rules), 010b (prompt + parser), 010c (persistence + matching + NEEDS_INFO), 010d (hand-checked gold + eval).

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md
- docs/08_TESTING_EVAL.md
- docs/07_SECURITY_COMPLIANCE.md

## Needs from owner
- Nothing.

## Scope
- `Requirement` schema (trip type, routes/multi-city, dates + flexibility, pax by age incl. infants, rooms, budget, preferences, nationality, special needs, urgency).
- Parser prompt v1 for English/Hindi/Hinglish with per-field confidence and source spans.
- Required-field rules per trip type; NEEDS_INFO loop that drafts clarifying questions; never assume silently.
- Customer matching and duplicate-inquiry detection.
- Eval set + runner against the gold inquiries.

## Acceptance criteria
- Required-field accuracy >= 95% on gold (record baseline if lower; do not fake it).
- Silent-assumption rate 0.
- Injection set: messages can't alter schema or trigger actions.
- Eval results stored per prompt version.

## Human gates / approvals
- Clarifying questions to customers are approval-gated (A1).

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-010: ...`, write the report from AGENTS.md, then STOP.
