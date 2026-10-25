# Task 008d1 — CRM import parsing and dry-run report

Phase 2 · Part of Task 008d · Depends on: 008c

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md
- docs/03_DATABASE.md
- docs/07_SECURITY_COMPLIANCE.md
- tasks/task_008_crm-core.md

## Needs from owner
- Customer workbook column names only; do not place real customer data in the repository.

## Scope
- Parse CSV and XLSX inputs in memory; validate configurable column mappings and report valid, invalid and possible duplicate rows without returning source PII.
- Support channel-specific marketing consent and proof-reference mappings. Missing, unknown, negative, unclear, or affirmative consent without proof grants no consent.
- Bound upload and expanded workbook sizes and reject malformed/unsupported files safely.
- Keep source and mapping digests opaque using the existing keyed fingerprint service.
- Do not add persistence or API writes in this slice.

## Acceptance criteria
- Synthetic tests cover CSV/XLSX, malformed and oversized files, mapping errors, duplicates, and channel-specific consent decisions.
- Dry-run report contains row numbers and safe issue codes, not raw customer values.
- Existing contact fingerprint values remain unchanged.

## Finish
Tests first, `make check` (or direct equivalents on Windows), docs updated, commit `task-008d1: ...`, report, then STOP.
