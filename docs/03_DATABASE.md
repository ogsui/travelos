# 03 Database

## Conventions
- PostgreSQL 16. UUID primary keys (time-sortable). `tenant_id`, `created_at`, `updated_at` (UTC) on every business table.
- Money: `<name>_paise BIGINT` + `currency CHAR(3)`. No float/numeric money columns (a test scans the models).
- Enums stored as text with CHECK constraints. JSONB only for raw provider payloads and immutable snapshots.
- (enc) = field-level encrypted. PII never appears in logs or LLM prompts unredacted.
- Append-only tables (`audit_log`, `llm_calls`, `price_snapshots`) have UPDATE/DELETE revoked or trigger-blocked. `requirements` is append-only at the ORM layer (see task 010c notes) until a DB trigger can coexist with the tenant-purge path. `outbox_events` is deliberately mutable: `status`, `attempts` and `available_at` move through the delivery state machine.
- Every external write carries an `idempotency_key` (unique).

## Identity & platform
- `users` (email, password_hash, role, mfa_secret(enc), active) · `sessions`
- `audit_log` (actor_type/id, action, subject_type/id, before, after, ip, at)
- `feature_flags` (key, value, scope, updated_by) — kill switches
- `autonomy_policies` (action_type, level A0-A3, value_threshold_paise, requires_four_eyes, updated_by, effective_from)
- `autonomy_metrics` (action_type, window, proposals, approved_unchanged, edited, rejected, incidents)
- `idempotency_keys` (key, scope, response_hash, expires_at)
- `outbox_events` (type, payload, status, attempts, available_at)
- `human_tasks` (type, subject, assignee_role, due_at, priority, status) — dead letters, escalations, to-dos
- `business_rule_versions` (yaml_hash, content, activated_by, activated_at)

## CRM & consent
- `customers` (type individual|family|corporate, display_name(enc), segment, owner_user_id, lifetime_value_paise, source_id)
- `contacts` (customer_id, channel, value(enc), value_hash HMAC-SHA256, verified, primary, language). The digest is normalized and channel-scoped; it is used for dedupe and suppression matching and never contains the plaintext value.
- `consents` (contact_id, channel, purpose marketing|transactional|reminders|reviews, status, source, proof_ref, granted_at, withdrawn_at)
- `suppression_list` (channel, value_hash, reason, at) — STOP/unsubscribe/DND, global
- `travellers` (customer_id, full_name_as_passport(enc), dob(enc), gender, passport_no(enc), passport_expiry(enc), nationality, is_minor, meal/seat prefs)
- `customer_links` (family/company relations) · `preferences` (customer_id, key, value, source booked|stated)
- `segments` (name, definition) · `customer_scores` (customer_id, rfm, lead_score, computed_at)

## Conversations & inquiries
- `conversations` (customer_id, contact_id, channel, external_thread_id, status, last_inbound_at) — tenant/channel/thread unique; drives the WhatsApp 24h window
- `messages` (conversation_id, direction, channel, body(enc), media_refs, template_id, status, external_id, occurred_at) — tenant/channel/external ID unique; normalized CRM timeline event
- `inquiries` (customer_id, conversation_id, source_id, state, trip_type, urgency, assigned_user_id, lost_reason, duplicate_of_inquiry_id, duplicate_evidence JSONB)
- `requirements` (inquiry_id, version, data JSONB (schema-validated), field_confidence JSONB, source_spans JSONB, missing_fields JSONB, prompt_name, prompt_version, provider, model, llm_call_id)
- `message_drafts` (inquiry_id, conversation_id, contact_id, approval_id UNIQUE, channel, body(enc), kind clarification|acknowledgement|sales_reply, template_key, question_codes, status draft|approved|rejected|sent, created_by, sent_at) - customer-facing text that is waiting for a human; no sender exists yet
- `conversation_handoffs` (inquiry_id, conversation_id, human_task_id UNIQUE, triggers JSONB, summary(enc), summary_version, created_by) - why a conversation went to a human and the encrypted staff summary
- `lead_sources` (channel, campaign_id, utm JSONB, referral_code, referrer_customer_id)

## Suppliers, market, pricing
- `suppliers` (name, type, adapter, status, credentials_ref) · `supplier_searches` (inquiry_id, params, started/finished, status)
- `price_snapshots` (supplier_id, product_type, payload JSONB, net_paise, taxes_paise, currency, fare_rules JSONB, fetched_at, expires_at, request_hash) — immutable
- `competitor_snapshots` (site, query_hash, payload JSONB, price_paise, comparability_score, fetched_at, status ok|blocked|unavailable)
- `pricing_rule_sets` (version, rules JSONB, approved_by, active_from) · `tax_rule_tables` (version, rules JSONB, approved_by_accountant, active_from)
- `price_calculations` (quote_revision_id, inputs JSONB, outputs JSONB, rule_version, explanation JSONB) — immutable
- `fx_rates` (pair, rate, source, at)

## Quotes
- `itineraries` (inquiry_id, option_tier, days JSONB) · `itinerary_items` (itinerary_id, component_type, snapshot_id, qty, details)
- `quotes` (inquiry_id, current_revision, state, expires_at, accept_token_hash)
- `quote_revisions` (quote_id, number, totals_paise, currency, pdf_ref, diff_from_previous JSONB, approval_id)
- `quote_lines` (revision_id, description, snapshot_id, cost_paise, price_paise, tax_paise)
- `quote_events` (quote_id, type sent|delivered|viewed|accepted|expired, at, channel)

## Approvals
- `approval_requests` (action_type, subject_type/id, requested_by, status pending|approved|edited|rejected|expired, level, proposed JSONB, final JSONB, reason, context JSONB, requires_four_eyes, approvals_needed/count, reviewer_id, second_reviewer_id, decided_at, expires_at, escalated_at, escalation_count). See the task 005 notes below.

## Payments, booking, billing
- `payment_schedules` (quote_id, instalments JSONB) · `payments` (customer_id, quote_id, gateway, link_id, amount_paise, state, gateway_ref UNIQUE, paid_at)
- `refunds` (payment_id, amount_paise, state, requested_by, executed_by_human_at, gateway_ref)
- `bookings` (inquiry_id, quote_id, state, confirmed_by_user_id, confirmed_at) · `booking_segments` (booking_id, supplier_id, type, supplier_ref, pnr, state, price_paise, rules JSONB, hold_expires_at, ticket_time_limit)
- `supplier_payments` (booking_id, supplier_id, amount_paise, paid_by_user_id, method, ref, at) — entered by humans
- `invoices` (number UNIQUE per series, type proforma|tax|credit_note|receipt, customer_id, booking_id, totals_paise, tax_breakup JSONB, state draft|approved|issued|void, approved_by_accountant, pdf_ref)
- `invoice_lines` · `ledger_entries` (account, debit_paise, credit_paise, booking_id, invoice_id, at)
- `exports` (type excel|tally|zoho|gst_pack, period, file_ref, created_by)

## Documents & support
- `documents` (booking_id, type, file_ref, checks JSONB, sent_at, channel) · `portal_links` (customer_id, token_hash, expires_at)
- `kb_articles` (title, body, source, approved_by, version) · `support_cases` (customer_id, booking_id, type, state, severity, assignee)
- `change_requests` (booking_id, type change|cancel|refund, computed_charges JSONB, state, approval_id)
- `disruptions` (booking_segment_id, type, detected_at, state)

## Post-trip & loyalty
- `feedback` (booking_id, rating, text, at) · `review_requests` (customer_id, channel, state)
- `testimonials` (customer_id, text, consent_ref NOT NULL, edited_by_customer_at, approved_by, published)
- `referral_codes` · `referral_rewards` (referrer_id, referred_booking_id, amount_paise, state)

## Outreach & marketing
- `message_templates` (channel, name, language, body, category, provider_status, approved_by)
- `campaigns` (name, objective, state, audience_def JSONB, audience_approved_by, budget_paise, caps JSONB, window) · `campaign_recipients` (campaign_id, contact_id, state, sent_at, delivered_at, replied_at, opted_out_at)
- `content_items` (type, channel, copy, assets, price_snapshot_refs, state, scheduled_at, approved_by) · `assets` (type, source own|licensed|ai_generated, licence_ref, file_ref)
- `social_accounts` (platform, handle, token_ref, status) · `social_posts` (content_item_id, platform, external_id, published_at) · `social_metrics` (post_id, at, impressions, engagement, clicks)
- `seo_pages` (slug, type, content, sources JSONB, fact_checked_by, published_at, last_refreshed_at) · `keyword_ideas`
- `ad_accounts` · `ad_campaigns` (platform, external_id, state, budgets JSONB, caps JSONB) · `ad_metrics` (campaign_id, date, spend_paise, clicks, leads, bookings)

## Analytics
Materialized views/marts: funnel_by_day, revenue_margin_by_product, lead_source_roi, campaign_roi, agent_performance, outstanding_payments, repeat_rate, response_times.

## Indexes & constraints to get right early
Unique: `messages(channel, external_id)`, `payments(gateway_ref)`, `invoices(series, number)`, `idempotency_keys(key, scope)`. FK everywhere. Partial indexes on `state` for hot queues (pending approvals, open quotes). CHECK: amounts >= 0, `consents.withdrawn_at >= granted_at`, `testimonials.consent_ref NOT NULL`.

## Implementation notes (task 002)
- Every table inherits `BaseTable` (`core/db.py`): UUIDv7 `id` PK, `tenant_id` (default tenant, indexed), `created_at`/`updated_at` (`timestamptz`, DB-side `now()`). UUIDv7 is generated in Python (`core/ids.py`), not by the database.
- Money columns follow the `<name>_paise BIGINT` + `<name>_currency CHAR(3)` pattern. `Money` (`core/money.py`) is the only money type in code; tests scan both the SQLAlchemy metadata and `information_schema` and fail on any float/numeric column.
- Encrypted columns (`contacts.value`, `users.mfa_secret`, `travellers.full_name_as_passport`, `dob`, `passport_no`, `passport_expiry`) use `EncryptedString`/`EncryptedDate`, which store `enc:v1:<fernet-token>`. The key is a 32-byte URL-safe base64 value in `ENCRYPTION_KEY`, read through the `KeyProvider` interface (`security/encryption.py`). Ciphertext is masked in logs before any other redaction rule.
- Contact fingerprints use HMAC-SHA256 with a domain-separated key derived from the same `KeyProvider` root key and a versioned context. The channel is included in the digest input. Rotate `ENCRYPTION_KEY` only through a coordinated data re-encryption and fingerprint rebuild; never change the root key without migrating both encrypted fields and contact/suppression digests.
- `audit_log` is append-only twice: SQLAlchemy listeners raise `AppendOnlyError` in-process, and a PostgreSQL trigger (`travelos_block_append_only`) blocks UPDATE, DELETE and TRUNCATE for every client. Migration `0001` installs it; `downgrade()` removes both trigger and function.- Schema tests run against a real PostgreSQL 16 started by the `pgserver` dev dependency (no Docker, no external database). Migration tests get a throwaway database each and exercise upgrade, seeded downgrade and re-upgrade.

## Auth, sessions, audit & flags (task 003)
- `sessions` stores only the SHA-256 hash of a 256-bit opaque token (`token_hash`, 64 hex chars), never the token itself. A row carries `user_id` (FK, `ON DELETE CASCADE`), `expires_at`, `revoked_at`, `last_seen_at`, `ip` and `user_agent`. Indexes: `uq_sessions_token_hash`, `ix_sessions_user_id`, `ix_sessions_expires_at`; CHECK `expires_at > created_at`. TTL comes from `SESSION_TTL_MINUTES` (default 720). Logout sets `revoked_at` instead of deleting the row, so the audit trail survives.
- Migration `0002_sessions` adds the table. API tests clone a fully migrated template database (`CREATE DATABASE ... TEMPLATE`) so each test gets a clean schema in milliseconds.
- `audit_log` writes go through `core.audit.AuditWriter`. `record()` stores redacted JSON payloads; `record_change()` stores only fields that actually differ (`build_diff`), with credentials, emails, phones, passport numbers and ciphertext masked. The writer joins the caller's transaction: a state change and its audit row commit or roll back together. Login success/failure, MFA outcomes, logout, permission denials and flag changes are all recorded.
- `feature_flags` is the owner-level switch (scope `global`, `value = {"enabled": bool}`, `updated_by`). Absent row means OFF. `core.features.FeatureFlagService` combines it with the deployment kill switch (`KILL_SWITCHES__<NAME>`): an operator OFF cannot be overridden by a database row, and an operator ON still needs an explicit owner row before the capability runs. Reads are cached per `(tenant, key, scope)` for 30 seconds; every write invalidates the key immediately. Malformed JSON values fail closed.
- Permission denials are committed as `auth.permission_denied` before the 403 is returned, so probes against forbidden routes leave a trail. All flag writes require `flags:write`, which only the owner role holds.

## Workflow engine, timers & outbox (task 004)
- `workflow_timers` (name, subject_type/id, due_at, payload JSONB, status, fired_at, cancelled_at). One row per `(tenant_id, name, subject_type, subject_id)` (`uq_workflow_timers_subject`), so re-arming a reminder updates `due_at` instead of inserting duplicates. `ix_workflow_timers_status_due` serves the due scan; a CHECK permits only `scheduled`, `fired`, `cancelled`.
- Migration `0003_workflow_timers` adds the table. Timers are one-shot: `TimerService.fire_due` marks due rows `fired` and writes a `timer.fired` outbox event plus a `workflow.timer_fired` audit row in the same transaction. Re-arming a fired timer is allowed; firing is guarded by `FOR UPDATE SKIP LOCKED` and the status transition, so duplicate beat runs cannot fire once more.
- `outbox_events` is written in the producer's transaction. The dispatcher claims due rows with `FOR UPDATE SKIP LOCKED`, calls one handler, then marks `done`, schedules a jittered exponential retry (`attempts`, `available_at`, `last_error`) or marks the event `dead`. Defaults: 5 attempts, 10 s base, 1 h cap, 20% jitter, batches of 25 (`OUTBOX_*` settings).
- Dead letters create a `human_tasks` row (`task_type=outbox.dead_letter`, `priority=high`, `assignee_role=admin`) and are not silently dropped. An event type with no registered handler is dead-lettered on its first delivery.
- Every handler runs inside a nested transaction and an `idempotency_keys` claim (`key=outbox event id`, `scope=outbox`). A redelivery, or a worker crash after the side effect but before commit, cannot run the handler twice. The claim and the side effect share the transaction, so a rolled-back attempt is retried cleanly.
- `WorkflowService.apply` is stateless: the domain table owns its `state` column. It fires the pure machine, writes one `workflow.transition` audit row (`before.state`, `after.state/trigger/machine`) and one outbox event per declared effect. Rejected transitions write nothing.
- Timer defaults (`inquiry.sla` 15 min, `inquiry.follow_up`/`payment.reminder` 24 h, `quote.expiry` 7 days, `booking.hold_expiry` 30 min) are placeholders. Callers may pass an explicit `delay`/`at`; owner-approved values from `config/business_rules.yaml` should override them when those rules are wired in.

## Approval engine & autonomy policies (task 005)
- `approval_requests` stores the proposal (`proposed`), the final payload after an edit/approve (`final`), the requesting user and the one or two reviewers. CHECK constraints limit status to `pending|approved|edited|rejected|expired`, level to `A0..A3`, `approvals_needed` to `1|2`, and require `status='approved'` to have all needed approvals. Indexes serve the pending queue (`tenant_id,status,created_at`), per-action metrics, subject lookup and expiry scans.
- Migration `0004_approval_requests` adds the table. `migrations/env.py` imports `travelos.approval.models`, and `tests/integration/test_schema.py` includes the table and its indexes in the schema contract.
- Autonomy is code-defined in `approval/matrix.py`: each of the 45+ actions carries `default_level`, `max_level`, `locked`, `owner_only` and optional `four_eyes`. A database row can lower the effective level but is clamped to `max_level`; LOCKED rows are always forced back to the catalogue default. Tampering with a row (or seeding a bad value) cannot make a LOCKED action automatic.
- `ApprovalService.submit` resolves A0 -> deny (no writes), A1 -> pending request, A2/A3 -> auto. Auto actions still write an `approval.auto_approved` audit row and outbox event. A pending request schedules `approval.escalation` and `approval.expiry` timers, creates an `approval.requested` human task and emits an outbox event in the same transaction.
- Decisions are `approve|edit|reject`. Reject requires a non-empty reason; edit requires a different final payload. The requester cannot decide their own request, and `owner_only` actions need an owner reviewer. Four-eyes requests (catalogue flag, policy flag or `value_paise >= value_threshold_paise`) need two distinct reviewers; the first approval leaves the request `pending` and records the first reviewer.
- `approval.decided`, `approval.escalated`, `approval.expired` and `autonomy.*` audit rows use `record_change`/`record`, and corresponding outbox events let dashboards and notifications react. `autonomy_metrics` is bumped daily per action (`proposals`, `approved_unchanged`, `edited`, `rejected`, `incidents`).
- The `travelos.autonomy.demote` beat task runs daily. It demotes one level when incidents > 0 in the last 30 days or when at least 50 proposals have an approved-unchanged rate below 85%. An `idempotency_keys` claim per action/metric row stops duplicate beat runs from demoting twice; LOCKED actions and A0 policies are never touched. Each demotion writes `autonomy.demoted`, an owner `human_tasks` row and an outbox event.
- The worker's single `timer.fired` handler routes approval timers and raises `UnhandledTimerError` for every other timer name, so unknown timers still dead-letter instead of disappearing.
- `admin/seed.py` is now idempotent: it inserts missing policies at catalogue defaults, repairs rows above their ceiling (and any LOCKED row that differs from its default), and leaves safer owner changes (a lower level) untouched. It refuses live mode and logs a warning instead of failing when the database is unavailable.
- Defaults `APPROVAL_ESCALATION_MINUTES=30` and `APPROVAL_TTL_MINUTES=1440` are safety timings, not business policy. The settings validator rejects an escalation at or after expiry. Four-eyes monetary thresholds default to `0` (disabled) until the owner sets them; see `docs/BACKLOG.md`.

## LLM calls & prompt metadata (task 006)
- `llm_calls` is one row per actual provider attempt, including each repair and each failed transport attempt. Columns: provider, model, prompt name/version, data class, status, input/output tokens, integer `cost_paise` + `currency`, latency, `cached`, `repairs`, `request`/`response` JSONB (redacted), SHA-256 request/response hashes, error and `metadata_json`.
- Statuses are `success|schema_error|provider_error|all_providers_failed|policy_blocked|budget_blocked`. A policy or budget block writes one row with no provider. When every provider fails, the gateway writes the per-attempt rows plus one aggregate row and a `human_tasks` row (`llm.provider_failure` or `llm.schema_failure`).
- The stored request/response never contains raw PII: reversible placeholders (`[[EMAIL_1]]`) stay placeholders in the row, and the log redactor runs again as a backstop. The placeholder-to-original mapping lives only in process memory for the duration of the call.
- Migration `0005_llm_calls` adds the table, its CHECK constraints and indexes, plus `llm_calls_block_mutation` and `llm_calls_block_truncate` triggers using the shared `travelos_block_append_only()` function. The migration replaces that function so its error message names the table instead of always saying `audit_log`; behaviour is unchanged.
- Migration `0006_llm_retention` adds `ix_llm_calls_created_at` and `ix_llm_calls_tenant_created_at` (retention scans and budget accounting) and extends the trigger function: `DELETE` on `llm_calls` is allowed only while the transaction-scoped setting `travelos.llm_retention_purge` is exactly `on`. UPDATE and TRUNCATE stay blocked. This is an accident guard, not a privilege boundary: the application role could set the flag, so a stronger separation would need a dedicated purge role.
- `llm_calls` is append-only for normal code at both layers (ORM listeners raise `AppendOnlyError`; PostgreSQL blocks UPDATE/DELETE/TRUNCATE). `LLMCallRetentionService` is the single deletion path: it sets the maintenance flag around one `DELETE`, clears it immediately, and writes an `llm.calls_purged` audit row (count, cutoff, retention days, optional tenant) without any prompt content. The `travelos.llm.purge_expired` beat task runs daily at 03:30 in the business timezone with `LLM_CALL_RETENTION_DAYS` (default 90).
- Daily budget accounting sums `cost_paise` for the tenant since local midnight in `LLM_ACCOUNTING_TIMEZONE` (default `Asia/Kolkata`), not UTC. A configured budget of `0` means "free providers only" and blocks unless a provider declares `ProviderPricing.free=True`; `None` means no enforced cap outside live. Live mode requires an explicit value.
- A call row is written in the caller's transaction, and each row also writes an `llm.call_succeeded` or `llm.call_failed` audit entry with provider, model, prompt version, token counts, cost and cache/repair flags.

## Synthetic data seeding (task 007)
- `make seed` (or `python -m travelos.admin.seed`) writes `data/synthetic/*.jsonl`, `data/synthetic/manifest.json` and `data/eval/adversarial_messages.jsonl` from `SYNTHETIC_SEED` (default `20261025`) before touching the database. The generator is pure and seeded: the same seed produces byte-identical files. If the database is unavailable the files are still written and a warning is logged; write failures return exit code 1.
- Only CRM tables exist at task 007, so the database seed inserts `customers`, `contacts`, `consents`, `suppression_list` and `travellers`. IDs are UUIDv5 values derived from the seed and the fixture natural ID, so a second run inserts zero rows. Customer, contact and traveller PII is written through `EncryptedString`/`EncryptedDate` and remains ciphertext at rest; contact `value_hash` is the normalized, channel-scoped HMAC fingerprint defined in task 008c. Migration `0008` converts earlier synthetic SHA-256 placeholder digests online.
- The mapper classes declare no ORM relationships, so `seed_crm` flushes customers, then contacts/suppressions, then consents/travellers explicitly. Without those flushes the unit of work cannot infer the foreign-key order.
- `data/schemas/*.schema.json` are the record contracts, validated with the task 006 JSON Schema subset in `travelos.llm.schema.validate_json`. The committed manifest records the seed, generator version, count and SHA-256 per file, so a hand-edited fixture fails `tests/unit/test_synthetic_data.py`.

## CRM consent service (task 008a)
- `ConsentService` is the shared decision point for outbound eligibility. `can_send(contact, channel, purpose)` returns false when the contact is missing from the tenant, the requested channel does not match the contact, the latest matching consent is absent or withdrawn, or a matching global suppression entry exists.
- Grant and withdrawal operations write audit records in the same transaction. Grant creates a consent row with source/proof reference; withdrawal records `status=withdrawn` and `withdrawn_at`. A new grant after a withdrawal is a new row, preserving the earlier decision history.
- `POST /crm/consents` and `POST /crm/consents/{id}/withdraw` require `crm:write`; `GET /crm/contacts/{id}/can-send` requires `crm:read`. All endpoints are tenant-scoped. Every outbound adapter must call the service before sending; adapters arrive in later channel tasks.

## CRM records (task 008b)
- `CRMService` provides tenant-scoped customer/contact/traveller create and read operations. State changes write audit metadata without names, contact values, DOB, or passport numbers.
- Customer display names, contact values, traveller names, DOBs and passport fields use field encryption. Migration `0007` encrypts existing customer names online and removes the unusable plaintext-name index; offline SQL generation is rejected because it cannot safely transform existing personal data. Downgrade decrypts the values before restoring the old column type.
- `/crm/customers` and customer child-record routes require `crm:read` for reads and `crm:write` for writes. `CRMService` creates a keyed contact fingerprint alongside each contact; `can_send` fails closed when an older record still lacks a digest.

## CRM import (task 008d)
- CSV/XLSX import accepts explicit mappings from source headers to supported CRM fields. Preview returns row-level errors and duplicate candidates without echoing customer values. Raw workbooks are not persisted.
- `/crm/imports/preview` creates a `crm.bulk_import` A1 approval request containing only keyed source/mapping digests and aggregate counts. `/crm/imports/apply/{approval_id}` re-reads the same source and writes only after that exact batch is approved; a permanent `idempotency_keys` claim prevents replay. Import writes and audit rows share the request transaction.
- Marketing consent mappings are channel-specific and consent is granted only for an explicit affirmative value plus a proof reference for that same channel. Missing, unclear, negative, or affirmative-without-proof values create no marketing consent.

## CRM merges (tasks 008e1–008e2)
- `/crm/merges/preview` creates a locked A1 `crm.customer_merge` approval tied to a keyed digest of both customers, linked contacts, contact consent rows and travellers. The preview shows source/target names and before/after counts to the authorized CRM operator and flags same-channel overlapping contact fingerprints without exposing contact values.
- `/crm/merges/apply/{approval_id}` locks and rechecks the exact approval snapshot before moving source contacts and travellers. Consent remains bound to its contact. The source is marked with `merged_into_customer_id` and excluded from active customer lists; linked-record creation against it is blocked.
- `customer_merge_history` stores only IDs, original/current customer links, primary-contact flags, state and timestamps. `/crm/merges/{approval_id}/undo` locks the history and affected records, refuses stale relationships or a new untracked target primary, then restores the recorded links. Apply/undo and their audit records share their request transaction.

## Inquiries, requirements & clarifying drafts (task 010c)
- `inquiries` is the one row a workflow owns. `state` follows the `inquiry` machine (`RECEIVED -> PARSED -> NEEDS_INFO -> ...`); `trip_type`, `urgency` and `duplicate_*` are denormalized for the operator queue. Migration `0011` adds it with CHECK constraints and tenant/state, tenant/customer and duplicate indexes.
- `requirements` stores one immutable version per parse. `data` is the validated `Requirement` JSON; `field_confidence`, `source_spans` and `missing_fields` preserve the parser's evidence and the deterministic gap list. `prompt_name`, `prompt_version`, `provider`, `model` and `llm_call_id` keep eval results attributable to a prompt version. A re-parse appends a version; it never edits an earlier one. The append-only guard is an ORM listener (`before_update`/`before_delete`) so a future tenant-purge job can still remove rows in one controlled path. `uq_requirements_version` prevents two versions with the same number.
- Blocking gaps move the inquiry to `NEEDS_INFO`. `assert_ready_for_search` fails closed with `InquiryNeedsInfoError` while any required field is missing; recommended gaps (budget, origin for packages, end date for flights) never block.
- Duplicate detection is deterministic and explainable: a candidate within 30 days that shares the conversation, the customer or a keyed contact fingerprint is recorded in `duplicate_evidence` and linked by `duplicate_of_inquiry_id`. A `human_tasks` row (`inquiry.duplicate_suspected`) asks an agent to decide. Nothing is merged; customer merges remain a locked A1 action.
- `message_drafts` stores the customer-facing text a human may send: `body` is encrypted, `question_codes` is the reviewed gap vocabulary for clarifications, and `status` starts as `draft`. `kind` separates `clarification`, `acknowledgement` and `sales_reply`; `template_key` names the reviewed acknowledgement template when one was used. `approval_id` is unique, so one approval owns one draft. A CHECK requires `sent_at` when status is `sent`; this build never sets either, and no channel sender is wired.
- Sales drafts (task 011a) use the same table. An acknowledgement requires an owner-approved template (`owner_approved=True`); the shipped English text is a placeholder marked unapproved, so the service refuses it until the owner reviews the wording. Free-form reply text passes a deterministic claim guard that blocks any price, availability, policy, discount or hard promise without a source ref (`quote:...`, `inventory:...`, `kb:...`). The guard result kinds and source refs are stored in the approval context; the draft body itself is only in the encrypted column, never in `approval_requests.proposed`. No draft is sent and no LLM is called in this slice.
- `/inquiries` reads require `crm:read`; create, parse and clarify require `crm:write`. All queries are tenant-scoped. Parse derives message, channel and language from the newest inbound message in the inquiry's conversation unless a specific `message_id` is supplied.

## Conversation handoffs (task 011b)
- `conversation_handoffs` is the companion row for a `human_tasks` row of type `conversation.handoff`. `triggers` holds stable reason codes (`anger`, `complaint`, `booking_value`, `legal`, `visa`, `refund`, `loop`); the human-readable evidence lives inside `summary`, which is encrypted because it quotes customer messages and names. `human_task_id` is unique, so one task has at most one handoff record. Migration `0013` adds the table with `ix_conversation_handoffs_tenant_inquiry`.
- The task title contains only reason labels (`Handoff: complaint, booking value`), never customer text. Staff can therefore see the queue without exposing PII in an unencrypted column or log line.
- Detection and summarising are deterministic; no LLM is called. A repeated question counts as a loop at three exact repeats inside the policy window (7 days by default), and a handoff is created for any positive booking value or customer-stated budget. Repeated calls while a task is `open`/`in_progress` refresh the same task: new reason codes are merged, the title and routing are updated, and `sales.handoff_refreshed` records the diff. A `done` task allows a genuinely new handoff.
- Priority and routing are placeholders until the owner supplies a taxonomy and SLA: anger/complaint/legal/refund are `high`, everything else `normal`; booking value/legal/refund go to `owner`, the rest to `agent`. The queue itself (active tasks, assignment, completion) is not built yet; this slice only writes the rows.
