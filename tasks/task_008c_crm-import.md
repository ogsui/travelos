# Task 008c — CRM CSV/Excel import

Phase 2 · Part of Task 008 · Depends on: 008b

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md
- docs/03_DATABASE.md
- docs/07_SECURITY_COMPLIANCE.md
- tasks/task_008_crm-core.md

## Needs from owner
- Customer workbook column names only; do not place real customer data in the repository.

## Scope
- Add configurable CSV/XLSX column mappings, validation and duplicate detection with a dry-run report.
- Map unknown, missing or unclear marketing-consent values to no marketing consent.
- Require agent/owner approval before an import writes records.

## Acceptance criteria
- Bad rows are reported individually; valid rows and duplicate candidates are summarized.
- Import is idempotent for the same source batch and mapping.
- Tests use only synthetic data and cover consent mapping, duplicates and malformed rows.

## Finish
Tests first, `make check` (or direct equivalents on Windows), docs updated, commit `task-008c: ...`, report, then STOP.
