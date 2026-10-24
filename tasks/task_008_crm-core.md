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
- Customer/contact/traveller services and API; merge/dedupe with human confirmation.
- Consent service: grant/withdraw/check by contact x channel x purpose; suppression list; `can_send(contact, channel, purpose)` used by every outbound path.
- Excel/CSV importer with column mapping, validation report and consent-status mapping (unknown = no marketing).
- Unified timeline query across channels.

## Acceptance criteria
- `can_send` is false for unknown/withdrawn consent (tests).
- Importer handles duplicates and bad rows and produces a report.
- Merge requires approval and is reversible via audit.

## Human gates / approvals
- Customer merges and bulk imports need agent/owner approval.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-008: ...`, write the report from AGENTS.md, then STOP.
