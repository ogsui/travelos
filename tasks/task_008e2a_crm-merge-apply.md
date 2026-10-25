# Task 008e2a — Apply approved customer merge

Phase 2 · Part of Task 008e2 · Depends on: 008e2, 008e1

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md
- docs/03_DATABASE.md
- docs/05_APPROVAL_MATRIX.md
- docs/07_SECURITY_COMPLIANCE.md
- tasks/task_008e1_crm-merge-preview.md

## Scope
- Add the migration and model for source-customer merge markers and non-PII merge history.
- Apply only the unchanged, approved snapshot; transactionally move linked contacts and travellers while preserving contact-bound consent and primary-contact rules.
- Mark merged source customers, prevent new linked-record writes, and exclude them from active customer lists.
- Make apply replay-safe and audit the state change.

## Acceptance criteria
- Pending, edited, rejected, stale or cross-tenant merge requests change no CRM rows.
- Approved apply is transactional and replay-safe; consent remains attached to the same contacts.
- Synthetic tests cover overlap, primary contact conflicts, tenant boundaries and rollback behavior.

## Finish
Tests first, `make check` (or direct Windows equivalents), docs updated, commit `task-008e2a: ...`, report, then STOP.
