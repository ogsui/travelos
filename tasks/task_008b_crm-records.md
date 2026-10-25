# Task 008b — CRM customer, contact and traveller records

Phase 2 · Part of Task 008 · Depends on: 008a

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md
- docs/03_DATABASE.md
- docs/07_SECURITY_COMPLIANCE.md
- tasks/task_008_crm-core.md

## Scope
- Add tenant-scoped customer, contact and traveller services and authenticated API routes.
- Keep customer display names, passport/DOB/contact values encrypted at rest and out of logs.

## Acceptance criteria
- Customer, contact and traveller reads/writes enforce `crm:read`/`crm:write` and tenant isolation.
- Tests verify encrypted PII and API authorization.

## Follow-up
- Keyed contact fingerprints and safe synthetic-seed hash migration are handled by Task 008c.

## Human gates
- Do not merge customer records in this task; merge is handled in 008d.

## Finish
Tests first, `make check` (or direct equivalents on Windows), docs updated, commit `task-008b: ...`, report, then STOP.
