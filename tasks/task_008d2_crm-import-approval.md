# Task 008d2 — Approval-gated CRM import API

Phase 2 · Part of Task 008d · Depends on: 008d1

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md
- docs/03_DATABASE.md
- docs/05_APPROVAL_MATRIX.md
- docs/07_SECURITY_COMPLIANCE.md
- tasks/task_008d1_crm-import-parse.md

## Scope
- Register `crm.bulk_import` as locked A1 in the approval matrix, approval catalogue, and relevant tests/docs.
- Add preview and apply endpoints through ActionPolicy/approval handling; bind approvals to exact source and mapping digests.
- Make writes idempotent for a source batch and mapping, transactional with audit metadata, tenant-safe, and resistant to replay.
- Keep raw uploaded data and PII out of preview responses and logs.

## Acceptance criteria
- Preview does not persist imported customers; apply refuses absent, stale, mismatched, or insufficient approval.
- A1 approval is required for every write; repeated apply does not duplicate records.
- Failure rolls back all records and audit metadata; synthetic API and integration tests cover these paths.

## Finish
Tests first, `make check` (or direct equivalents on Windows), docs updated, commit `task-008d2: ...`, report, then STOP.
