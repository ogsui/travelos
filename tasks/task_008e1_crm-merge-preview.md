# Task 008e1 — Locked A1 CRM merge preview

Phase 2 · Part of Task 008e · Depends on: 008b, 008d2

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md
- docs/03_DATABASE.md
- docs/05_APPROVAL_MATRIX.md
- docs/07_SECURITY_COMPLIANCE.md
- tasks/task_008_crm-core.md

## Scope
- Register `crm.customer_merge` as locked A1 in the approval matrix/catalogue, tests and docs.
- Add a tenant-scoped preview endpoint that reports source/target names, linked record counts, projected target counts and same-channel overlapping contacts without exposing contact values.
- Bind the approval proposal to a keyed digest of both customer records, linked contacts, consent rows and travellers; changing the snapshot must require a new preview.
- Do not move, delete or otherwise change CRM records in this sub-task.

## Acceptance criteria
- A merge preview creates a pending A1 request; same-customer and cross-tenant proposals fail safely.
- Preview reports overlap by channel while keeping contact values and consent proof private.
- The preview is human-readable and no CRM state changes before approval.
- Synthetic route, approval matrix and access tests pass.

## Finish
Tests first, `make check` (or direct equivalents on Windows), docs updated, commit `task-008e1: ...`, report, then STOP.
