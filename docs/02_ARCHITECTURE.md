# 02 Architecture

## Style
Modular monolith, hexagonal (ports & adapters). Domain logic is pure Python. Every external system sits behind an interface with a mock. Modules talk through service calls and domain events (transactional outbox). One API, background workers, one dashboard, one public site.

## Runtime components
```
Channels: WhatsApp | Email | Web form | IG/FB DM | GBP | Phone log
        | webhooks
        v
   API (FastAPI) <----> Dashboard (Next.js: approvals, inbox, CRM, analytics)
        |   \
        |    +--> PostgreSQL (system of record)   Redis (queues, cache, locks)
        |    +--> Object store (PDFs, media)
        v
   Workers (Celery): workflows, supplier calls, LLM jobs, rendering, campaigns
   Beat: timers (expiry, reminders, schedules, nightly jobs)
   Portal price worker (Playwright): isolated container, owner-authorized accounts only
        |
   LLM Gateway --> providers (policy-gated by data class)
```

## Stack
Python 3.12, FastAPI (sync endpoints), SQLAlchemy 2 + Alembic, PostgreSQL 16, Redis, Celery + beat, Pydantic v2, httpx, tenacity, WeasyPrint, Playwright, openpyxl, Next.js + TypeScript (dashboard + public site), MinIO/S3, Docker Compose. Observability: structured JSON logs, Prometheus metrics, Sentry-compatible error reporting.

## Module map (`src/travelos/`)
| Module | Responsibility |
|---|---|
| core | settings, modes, flags, audit, money, ids, time, errors, idempotency, outbox |
| security | auth, RBAC, encryption, redaction |
| workflow | state machines, timers, jobs |
| approvals | action policies, approval requests, graduation tracking |
| llm | gateway, prompt registry, redaction, evals |
| crm | customers, travellers, consent, segments, import |
| channels | whatsapp, email, sms, instagram, facebook, gbp, webform, mock |
| inquiry | requirement schema, parser, missing-info logic |
| conversation | sales/support drafting, handoff rules, follow-up cadences |
| suppliers | normalized models, simulator, registry, parallel search, snapshots, authorized portal adapters |
| packages | deterministic itinerary builder, occupancy/age rules, option tiers |
| market | competitor price adapters, comparability |
| pricing | deterministic price engine, rules |
| quotes | quote revisions, PDFs, acceptance |
| payments | gateway adapter, schedules, reconciliation |
| booking | revalidation, booking orchestration, references |
| billing | invoices, ledger, exports, tax tables |
| documents | itinerary/voucher bundles, portal, checks |
| support | KB, intents, change/cancel workflows, disruption |
| postsales | feedback, reviews, referrals, occasion messages |
| outreach | campaigns, audiences, templates, caps |
| marketing | content engine, social publishing, ads, seo |
| analytics | marts, dashboards, digests |
| orchestrator | manager: SLAs, routing, graduation, digests |
| admin | config screens, seed, diagnostics |

Layering inside each module: `domain/` (pure) -> `services/` -> `adapters/` -> `api/`. Domain never imports adapters.

## Core flow: inquiry to invoice
1. Message arrives on a channel adapter -> normalized into tenant-scoped `Conversation` and encrypted `Message` records -> consent/identity matched -> `Inquiry` created (RECEIVED). CRM timeline reads normalized messages newest first.
2. Acknowledgement from approved template (A3 once enabled).
3. Parser builds a `Requirement`; missing fields -> NEEDS_INFO with clarifying questions (approval per matrix).
4. Supplier searches (parallel, with timeouts) -> immutable snapshots. Competitor snapshots fetched if enabled.
5. Package builder -> option tiers. Pricing engine -> floor/recommended/ceiling + explanation.
6. Quote draft + PDF -> PENDING_APPROVAL. Human reviews diff-style screen -> approves/edits/rejects.
7. Quote sent; follow-up cadence starts. Customer accepts -> payment link/schedule.
8. Payment recorded by webhook; human verifies and funds the supplier (human-only).
9. Booking orchestrator: revalidate fare -> human confirms -> idempotent booking -> store references.
10. Billing: invoice + receipt + voucher drafts -> accountant approval (per matrix) -> ledger -> master Excel updated.
11. Documents delivered; support agent active; post-trip sequence scheduled.

## State machines (authoritative; implemented in task 004)
- **Inquiry**: RECEIVED -> PARSED -> NEEDS_INFO <-> PARSED -> SEARCHING -> PRICED -> PENDING_APPROVAL -> APPROVED -> QUOTE_SENT -> NEGOTIATING -> ACCEPTED -> AWAITING_PAYMENT -> PAID -> BOOKING -> BOOKED -> INVOICED -> DOCS_SENT -> TRAVELLING -> COMPLETED -> CLOSED. Exits: REJECTED, EXPIRED, LOST, CANCELLED.
- **Quote**: DRAFT -> PENDING_APPROVAL -> APPROVED -> SENT -> VIEWED -> ACCEPTED | EXPIRED | REVISED (new revision) | REJECTED.
- **Booking**: PENDING_CONFIRMATION (human) -> REVALIDATING -> HELD -> TICKETING -> CONFIRMED | FAILED_PARTIAL -> NEEDS_HUMAN; CONFIRMED -> CANCEL_REQUESTED -> CANCELLED.
- **Payment**: LINK_CREATED -> PARTIAL -> PAID | FAILED | EXPIRED; PAID -> REFUND_REQUESTED -> REFUNDED (recorded by a human).
- **Campaign**: DRAFT -> AUDIENCE_PENDING -> CONTENT_PENDING -> SCHEDULED -> RUNNING -> PAUSED | COMPLETED.
- **ContentItem**: IDEA -> DRAFT -> PENDING_APPROVAL -> SCHEDULED -> PUBLISHED -> ANALYZED | REJECTED.

Implementation note (task 004): the definitions live in `workflow/definitions.py` as immutable data. `TICKETING -> FAILED_PARTIAL -> NEEDS_HUMAN` is the escalation path. Campaign additionally allows `PAUSED -> RUNNING` (`resume`) so an operator can recover a paused campaign; this is the only edge beyond the list above. Transition effects are event-type names written to the transactional outbox. Guards use fail-closed booleans (`approved`, `human_confirmed`, `audience_approved`, `content_approved`); a missing or non-`True` value rejects the transition.

## LLM architecture
All model calls go through `llm/gateway`:
- Providers: Fake (tests), OpenAI-compatible (covers OpenCode Zen, OpenRouter, local servers, and other compatible endpoints), Anthropic. Each has policy flags: `no_training`, `synthetic_only`.
- Data classes: `synthetic | customer | public`. A call with `customer` or `public` data requires a provider with `no_training=True` and `synthetic_only=False`; an ineligible call is blocked before any network request. A caller may escalate a prompt's declared class but never downgrade it.
- Placeholders replace PII before the call; the mapping stays in process memory and is restored in the answer. Stored requests/responses keep placeholders.
- Outputs are validated against the JSON Schema declared by the prompt; one repair retry; then the next provider, and finally a high-priority human task.
- LLMs never call tools directly: the request type has no tool/function field. They return proposals; the ActionPolicy layer decides (auto, approval, or deny).
- Every attempt is recorded in append-only `llm_calls` with integer-paise cost, tokens, latency, redacted bodies and hashes; budget checks and a development-only response cache sit in front of the provider chain. The budget day follows `LLM_ACCOUNTING_TIMEZONE` (IST), a ₹0 budget allows only explicitly free providers, and a nightly audited purge enforces the owner's 90-day retention.

## Reliability patterns
Timeouts everywhere; retries with jittered backoff; circuit breakers per adapter; idempotency keys on every external write; transactional outbox; dead-letter queue with a dashboard; graceful degradation (competitor data missing -> quote anyway, flagged); feature-flag kill switches; schema migrations backwards compatible.

## Environments
`local` (Docker, synthetic data, mock adapters) -> `staging` (synthetic data, sandbox adapters) -> `prod` (`MODE=shadow`, then `live`). Config via environment variables and `config/business_rules.yaml`. Secrets never in the repo.

## Repo layout
```
travelos/
├── AGENTS.md  opencode.json  README.md  Makefile  docker-compose.yml
├── .opencode/commands/       # /task /review /status
├── docs/  tasks/  prompts/  config/
├── src/travelos/<module>/{domain,services,adapters,api}
├── web/{dashboard,site}/
├── data/{synthetic,eval}/    # fake data only
├── migrations/  tests/{unit,integration,contract,eval,e2e}/
```
