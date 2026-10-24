# 09 Coding Standards

## Python
- 3.12, `mypy --strict`, `ruff` (lint + format), `pytest`, `hypothesis` for money/state logic. No `Any` without a comment.
- Pydantic v2 models at every boundary (API, adapters, LLM outputs, config). Domain objects are immutable where practical.
- SQLAlchemy 2.0 style, sync sessions, explicit transactions in services; Alembic migrations are backwards compatible (expand -> migrate -> contract).
- Functions short and single-purpose (aim < 50 lines). Prefer composition and small interfaces. No hidden global state; dependency injection via constructors/FastAPI dependencies.
- `Money(paise: int, currency: str)` is the only money type. Arithmetic helpers reject mixed currencies. Display formatting only at the edge.
- Time: store UTC; display `Asia/Kolkata`; inject a `Clock` so timers are testable.
- IDs: UUID (time-sortable). Never expose sequential internal IDs externally.
- Errors: typed exceptions in domain; adapters return `Result`/raise `AdapterError` subtypes (`Transient`, `Permanent`, `RateLimited`, `AuthFailed`). Never swallow exceptions silently.
- External calls: always timeout, retry only idempotent/transient, circuit breaker, idempotency key on writes.
- Logging: structured JSON, request/trace IDs, redaction filter for PII and secrets; no `print`.
- Config: `pydantic-settings`; no secrets in code; every flag documented in `.env.example` with a safe default.

## LLM code
- Only via `llm/gateway`. Prompts live in `prompts/` with versions; each prompt has an eval set and stored results.
- Untrusted content is passed as delimited data in the user message, never concatenated into the system prompt.
- Temperature low for parsing/extraction; schema-validated output; one repair retry; then human task.
- No business logic in prompts that code can do deterministically.

## Web (dashboard + site)
- Next.js + TypeScript strict, ESLint, component tests, Playwright e2e. Accessible and mobile-first (approvals are done from phones). No secrets in client bundles. Server-side authorization, not just hidden buttons.
- Public site: fast, SEO-friendly, consent banner, privacy notice, no third-party scripts without need.

## Testing
- Test first. Every module ships unit tests; adapters ship contract tests; workflows ship transition-table tests; LLM components ship evals.
- Tests use the `Clock`, the Fake LLM, mock adapters and synthetic data. No network in unit tests.
- Coverage is a signal, not a goal: money, state machines, consent, idempotency and approvals need near-complete branch coverage.

## Git & process
- Branch per task, one commit per task (`task-NNN: summary`), no force-push to main, small reviewable diffs.
- PR/commit description includes decisions and unresolved questions. Docs updated in the same commit.
- `docs/DECISIONS.md`: short ADRs for anything non-obvious (what, why, alternatives).

## Definition of done
Acceptance criteria met · tests + evals pass · `make check` green · docs updated · no TODOs without an owner question · no new secrets or real data · audit records added for new state changes · kill switch/flag added for any new outbound capability · report written.
