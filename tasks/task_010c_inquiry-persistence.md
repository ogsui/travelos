# Task 010c — Inquiry persistence, matching and NEEDS_INFO loop

Phase 2 · Part of Task 010 · Depends on: 004, 005, 008, 009, 010b

## Read first
- AGENTS.md
- docs/03_DATABASE.md
- docs/05_APPROVAL_MATRIX.md
- docs/07_SECURITY_COMPLIANCE.md
- src/travelos/approval/matrix.py
- src/travelos/channels/ (conversation/message model)
- src/travelos/crm/ (customer matching, fingerprints, merge)
- tasks/task_010a_requirement-schema.md, tasks/task_010b_parser-prompt.md

## Scope
- Alembic migration and SQLAlchemy models for `inquiries` and `requirements`
  (`data` JSONB, `field_confidence` JSONB, `source_spans` JSONB,
  `missing_fields`, version per inquiry).
- `inquiry/service.py`: persist a parse result, append a new requirement version,
  move the inquiry RECEIVED -> PARSED -> NEEDS_INFO via the workflow engine, and
  record audit entries.
- Customer matching and duplicate-inquiry detection: reuse keyed contact
  fingerprints; flag likely duplicates for a human instead of merging; merges
  remain locked A1.
- Draft clarifying questions for missing fields behind
  `message.clarifying_questions` (A1, cannot be configured away). Never send
  from this task.
- Authenticated API endpoints with tenant scoping and existing RBAC.

## Acceptance criteria
- Requirement rows are append-only versions; a re-parse never overwrites history.
- Missing required fields put the inquiry in NEEDS_INFO; no supplier search is
  started while a required field is missing.
- Clarifying questions are stored as an approval request and an outbound draft,
  never sent directly; the action is checked through the ActionPolicy layer.
- Duplicate detection is deterministic and explains its evidence (contact hash,
  channel, recent thread) without putting PII in logs.
- Migration upgrades, downgrades and re-upgrades on a seeded database.

## Finish
Tests first, run `make check` (or direct equivalents on Windows), update docs,
commit `task-010c: ...`, write the report from AGENTS.md, then STOP.
