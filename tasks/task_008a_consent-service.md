# Task 008a — CRM consent service and API

Phase 2 · Part of Task 008 · Depends on: 002, 003, 007

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md
- docs/03_DATABASE.md
- docs/07_SECURITY_COMPLIANCE.md
- tasks/task_008_crm-core.md

## Scope
- Provide tenant-scoped grant, withdrawal, and check operations for contact x channel x purpose.
- Check the global suppression list in the shared `can_send` decision.
- Add authenticated CRM consent endpoints with the existing RBAC and audit layers.

## Acceptance criteria
- Unknown or withdrawn consent, channel mismatch, and suppression all deny delivery.
- Grant and withdrawal decisions are audited without copying contact values into the audit payload.
- API access is tenant-scoped and enforces `crm:read`/`crm:write`.

## Finish
Tests first, run `make check` (or direct equivalents on Windows), update docs, commit `task-008a: ...`, write the report from AGENTS.md, then STOP.
