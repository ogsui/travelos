# Task 008c — Keyed contact fingerprints

Phase 2 · Part of Task 008 · Depends on: 008b

## Read first
- AGENTS.md
- docs/03_DATABASE.md
- docs/07_SECURITY_COMPLIANCE.md
- tasks/task_008_crm-core.md

## Scope
- Define normalized, keyed HMAC contact fingerprints for dedupe and suppression lookups.
- Safely migrate existing synthetic-seed SHA-256 placeholder hashes without exposing plaintext in logs.
- Reuse the same fingerprint convention for contact writes and suppression checks.

## Acceptance criteria
- Equivalent normalized values hash consistently for the same channel and secret; different channels/secrets do not collide.
- No plaintext contact value is logged or stored in the digest column.
- Existing synthetic fixtures re-seed idempotently with keyed fingerprints.

## Finish
Tests first, `make check` (or direct equivalents on Windows), docs updated, commit `task-008c: ...`, report, then STOP.
