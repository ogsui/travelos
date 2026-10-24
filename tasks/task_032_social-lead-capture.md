# Task 032 — Instagram/Facebook/GBP/phone lead capture

Phase 4 · Depends on: 009, 008 · Requirements: CH-1, CH-2, CH-3

## Read first
- AGENTS.md
- docs/04_INTEGRATIONS.md
- docs/07_SECURITY_COMPLIANCE.md

## Needs from owner
- Meta app review status, GBP access.

## Scope
- Adapters for Instagram DM, Messenger, lead-ads webhooks, Google Business Profile messages, phone-call log entries.
- Cross-channel identity matching and dedupe; source attribution.

## Acceptance criteria
- Same person across channels maps to one customer with human-confirmed merges.
- Webhook fixtures pass.
- Consent captured per channel.

## Human gates / approvals
- Replies follow the matrix.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-032: ...`, write the report from AGENTS.md, then STOP.
