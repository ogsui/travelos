# Task 008e — Approval-gated reversible CRM merge

Phase 2 · Part of Task 008 · Depends on: 008b, 008d

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md
- docs/03_DATABASE.md
- docs/05_APPROVAL_MATRIX.md
- docs/07_SECURITY_COMPLIANCE.md
- tasks/task_008_crm-core.md

## Scope
- Propose duplicate customer merges with a human-readable before/after preview.
- Require agent/owner approval before moving linked contacts, consents or travellers.
- Record enough non-PII audit/merge history to reverse an approved merge.

## Acceptance criteria
- No merge occurs before approval; rejected proposals leave records unchanged.
- An approved merge is reversible and both directions are audited.
- Tenant boundaries and duplicate/overlapping contact cases are tested.

## Finish
Tests first, `make check` (or direct equivalents on Windows), docs updated, commit `task-008e: ...`, report, then STOP.
