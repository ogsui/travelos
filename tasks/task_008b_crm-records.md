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
- Define normalized, keyed contact fingerprints for dedupe and suppression lookups; migrate synthetic-seed hashes safely.
- Keep passport/DOB/contact values encrypted at rest and out of logs.

## Acceptance criteria
- Customer, contact and traveller reads/writes enforce `crm:read`/`crm:write` and tenant isolation.
- Duplicate contact lookup uses a keyed digest; no plaintext contact value is logged or stored in the digest column.
- Tests verify encrypted PII and API authorization.

## Human gates
- Do not merge customer records in this task; merge is handled in 008d.

## Finish
Tests first, `make check` (or direct equivalents on Windows), docs updated, commit `task-008b: ...`, report, then STOP.
