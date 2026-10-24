# Task 037 — Observability, backups, deployment

Phase 5 · Depends on: 001 · Requirements: OPS-1

## Read first
- AGENTS.md
- docs/02_ARCHITECTURE.md
- docs/07_SECURITY_COMPLIANCE.md

## Needs from owner
- Hosting choice and budget.

## Scope
- Metrics, tracing, alerts (queue depth, error rates, approval latency, LLM cost, supplier health); uptime checks.
- Backups + restore drill script; staging/prod deployment; CI/CD with rollback; runbooks (docs/runbooks/).

## Acceptance criteria
- Restore drill succeeds from a backup (documented).
- Alerts fire in test.
- Rollback rehearsed.

## Human gates / approvals
- Production deploys need owner/admin approval.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-037: ...`, write the report from AGENTS.md, then STOP.
