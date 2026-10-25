# Task 008e2 — Apply and reverse approved CRM merges

Phase 2 · Part of Task 008e · Depends on: 008e1

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md
- docs/03_DATABASE.md
- docs/05_APPROVAL_MATRIX.md
- docs/07_SECURITY_COMPLIANCE.md
- tasks/task_008e1_crm-merge-preview.md

## Scope
- Add the migration and model for source-customer merge markers and non-PII merge history.
- Apply only the unchanged, approved snapshot; transactionally move linked contacts and travellers while preserving contact-bound consent history and primary-contact rules.
- Mark merged source customers, prevent new linked-record writes, and exclude them from active customer lists.
- Add a tenant-scoped undo endpoint that restores the recorded relations and primary flags, rejects unsafe/stale reversals, and audits both directions.

## Acceptance criteria
- Unapproved, edited, rejected, stale or cross-tenant merges change no CRM rows.
- Approved merges are replay-safe; consent records remain attached to the same contacts.
- Undo restores the exact recorded relationships, and repeated undo is safe.
- Synthetic tests cover overlaps, primary contacts, rejected/stale proposals, tenant boundaries and rollback.

## Finish
Tests first, `make check` (or direct equivalents on Windows), docs updated, commit `task-008e2: ...`, report, then STOP.
