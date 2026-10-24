# Task 038 — Security hardening & privacy workflows

Phase 5 · Depends on: 003, 008, 037 · Requirements: CORE-2, CRM-2

## Read first
- AGENTS.md
- docs/07_SECURITY_COMPLIANCE.md

## Needs from owner
- Counsel/CA review items from docs/07.

## Scope
- Threat model, rate limiting, headers, dependency/secret scanning, encryption verification.
- DPDP workflows: consent withdrawal, data export/erasure (approval-gated), retention jobs.
- Red-team suite (injection, PII fishing) in CI; incident runbooks.

## Acceptance criteria
- Adversarial suite 100% contained.
- Erasure removes/anonymizes per policy and is audited.
- No secrets or PII found by scanners.

## Human gates / approvals
- Deletion LOCKED owner.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-038: ...`, write the report from AGENTS.md, then STOP.
