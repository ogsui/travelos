# Task 001 — Repo scaffold & dev environment

Phase 1 · Depends on: — · Requirements: CORE-1, OPS-1

## Read first
- AGENTS.md
- docs/02_ARCHITECTURE.md
- docs/09_CODING_STANDARDS.md

## Needs from owner
- Nothing.

## Scope
- Monorepo layout from docs/02 (`src/travelos`, `web/`, `tests/`, `migrations/`, `data/`).
- docker-compose: api, worker, beat, postgres, redis, minio, mailpit, web.
- Makefile: up, down, seed, test, lint, typecheck, check, eval.
- pydantic-settings config with MODE (test|shadow|live, default test) and per-capability kill switches (all default OFF).
- Structured JSON logging with request IDs and a redaction filter; /health and /ready endpoints.
- CI workflow (lint, mypy --strict, tests), pre-commit, `.env.example`, `.gitignore`.

## Acceptance criteria
- `make up` boots all services; `make check` is green on the empty app.
- `MODE=live` refuses to start without `LIVE_CONFIRMED=yes` and required settings (tested).
- AGENTS.md 'Stack & commands' section is accurate.

## Human gates / approvals
- None (no outbound capability yet).

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-001: ...`, write the report from AGENTS.md, then STOP.
