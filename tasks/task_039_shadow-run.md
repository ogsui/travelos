# Task 039 — 30-day shadow run tooling

Phase 6 · Depends on: 018, 038 · Requirements: APR-4

## Read first
- AGENTS.md
- docs/08_TESTING_EVAL.md
- docs/10_ROADMAP.md

## Needs from owner
- Staff time to copy in real inquiries and log what they did.

## Scope
- Read-only intake of real inquiries (forwarding or channel mirror) in `MODE=shadow`; staff feedback capture UI; weekly review report; go/no-go checklist.

## Acceptance criteria
- Nothing leaves the system in shadow mode (test with network deny).
- Weekly report generated from real data.

## Human gates / approvals
- Owner signs go/no-go.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-039: ...`, write the report from AGENTS.md, then STOP.
