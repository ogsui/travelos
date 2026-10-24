# Task 004 — Workflow engine, timers, outbox

Phase 1 · Depends on: 002, 003 · Requirements: CORE-4

## Read first
- AGENTS.md
- docs/02_ARCHITECTURE.md
- docs/09_CODING_STANDARDS.md

## Needs from owner
- Nothing.

## Scope
- Generic state-machine library: states, guards, side effects emitted through the outbox, transition audit.
- Definitions for Inquiry, Quote, Booking, Payment, Campaign, ContentItem exactly as in docs/02.
- Celery worker + beat; timers (SLA, expiry, reminders) driven by an injectable Clock.
- Retries with backoff, dead-letter queue writing `human_tasks`, idempotent consumers.

## Acceptance criteria
- Illegal transitions rejected (table-driven property test).
- Timers fire correctly with a fake clock.
- Duplicate event delivery has no double side effects.
- DLQ items appear as human tasks.

## Human gates / approvals
- None.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-004: ...`, write the report from AGENTS.md, then STOP.
