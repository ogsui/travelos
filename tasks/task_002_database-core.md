# Task 002 — Core database, money type, encryption

Phase 1 · Depends on: 001 · Requirements: CORE-2, CORE-3, CORE-6, CRM-1

## Read first
- AGENTS.md
- docs/03_DATABASE.md
- docs/07_SECURITY_COMPLIANCE.md

## Needs from owner
- Nothing.

## Scope
- SQLAlchemy models + Alembic for: users, audit_log, feature_flags, autonomy_policies, autonomy_metrics, idempotency_keys, outbox_events, human_tasks, business_rule_versions, customers, contacts, consents, suppression_list, travellers.
- `Money` value type (integer paise + currency) and a lint/test that fails on float money columns.
- Field-level encrypted column type (key from env via a `KeyProvider` interface) for contacts, passport data, DOB.
- `tenant_id`, timestamps, UUID PKs on all tables; append-only enforcement for audit_log.
- Required indexes/constraints from docs/03 for these tables.

## Acceptance criteria
- Migrations apply and roll back cleanly on empty and seeded DBs.
- Encryption round-trip test; ciphertext never appears in logs (test).
- Attempted UPDATE/DELETE on audit_log fails (test).
- Constraint tests for consents and money.

## Human gates / approvals
- None.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-002: ...`, write the report from AGENTS.md, then STOP.
