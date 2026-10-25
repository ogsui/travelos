# Task 008e2b — Guarded customer merge undo

Phase 2 · Part of Task 008e2 · Depends on: 008e2a

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md
- docs/03_DATABASE.md
- docs/05_APPROVAL_MATRIX.md
- docs/07_SECURITY_COMPLIANCE.md
- tasks/task_008e2a_crm-merge-apply.md

## Scope
- Add a tenant-scoped undo endpoint using the non-PII merge history created by 008e2a.
- Restore original contact/traveller ownership and primary-contact flags only when the recorded post-merge state remains safe.
- Make repeated undo safe and audit the reverse state transition.

## Acceptance criteria
- Undo rejects missing, cross-tenant, stale or unsafe history without changing CRM rows.
- Successful undo restores the recorded relationships and primary flags; consent stays attached to its contact.
- Repeated undo is safe and both directions are audited.

## Finish
Tests first, `make check` (or direct Windows equivalents), docs updated, commit `task-008e2b: ...`, report, then STOP.
