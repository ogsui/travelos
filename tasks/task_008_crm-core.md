# Task 008 — CRM core, consent service, import

Phase 2 · Depends on: 002, 003, 007 · Requirements: CRM-1, CRM-2, CRM-3, CRM-4

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md
- docs/03_DATABASE.md
- docs/07_SECURITY_COMPLIANCE.md

## Needs from owner
- A sample of the agency's customer Excel layout (columns only, no real data in the repo).

## Scope
- This task is split into bounded sub-tasks because its full scope exceeds the repository's ~800 changed-line limit:
  - 008a: Consent service/API and fail-closed `can_send` (current sub-task).
  - 008b: Customer/contact/traveller services and API, including encrypted customer names.
  - 008c: Keyed contact fingerprints and safe synthetic-seed hash migration (complete).
  - 008d: Mapping-based CSV/Excel importer and validation report.
  - 008e: Human-confirmed, reversible customer merge/dedupe.
- Unified communication timeline moves to Task 009, after normalized channel events exist; Task 009 must include the CRM timeline query.

## Acceptance criteria
- Each sub-task owns its corresponding acceptance criteria and tests.
- `can_send` is false for unknown/withdrawn consent (008a).
- Importer handles duplicates and bad rows and produces a report; unknown consent maps to no marketing (008d).
- Merge requires approval and is reversible via audit (008e).

## Human gates / approvals
- Customer merges and bulk imports need agent/owner approval.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-008: ...`, write the report from AGENTS.md, then STOP.
