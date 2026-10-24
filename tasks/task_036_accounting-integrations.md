# Task 036 — Excel/Tally exports & reconciliation

Phase 5 · Depends on: 021 · Requirements: BIL-4

## Read first
- AGENTS.md
- docs/06_PRICING_ENGINE.md
- docs/07_SECURITY_COMPLIANCE.md
- docs/11_BUSINESS_RULES_INTERVIEW.md

## Needs from owner
- The agency's master Excel/tax workbook and accountant requirements.

## Scope
- Update the master workbook (openpyxl) on invoice issue; monthly accountant pack; Tally/Zoho export formats; payments vs supplier invoices reconciliation report.

## Acceptance criteria
- Workbook output matches the owner's existing layout (side-by-side check).
- Re-running an export is idempotent.

## Human gates / approvals
- Accountant approves formats.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-036: ...`, write the report from AGENTS.md, then STOP.
