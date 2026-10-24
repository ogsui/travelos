# 03 Database

## Conventions
- PostgreSQL 16. UUID primary keys (time-sortable). `tenant_id`, `created_at`, `updated_at` (UTC) on every business table.
- Money: `<name>_paise BIGINT` + `currency CHAR(3)`. No float/numeric money columns (a test scans the models).
- Enums stored as text with CHECK constraints. JSONB only for raw provider payloads and immutable snapshots.
- (enc) = field-level encrypted. PII never appears in logs or LLM prompts unredacted.
- Append-only tables (`audit_log`, `llm_calls`, `price_snapshots`, `outbox_events` history) have UPDATE/DELETE revoked or trigger-blocked.
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
- `customers` (type individual|family|corporate, display_name, segment, owner_user_id, lifetime_value_paise, source_id)
- `contacts` (customer_id, channel, value(enc), verified, primary, language)
- `consents` (contact_id, channel, purpose marketing|transactional|reminders|reviews, status, source, proof_ref, granted_at, withdrawn_at)
- `suppression_list` (channel, value_hash, reason, at) — STOP/unsubscribe/DND, global
- `travellers` (customer_id, full_name_as_passport(enc), dob(enc), gender, passport_no(enc), passport_expiry(enc), nationality, is_minor, meal/seat prefs)
- `customer_links` (family/company relations) · `preferences` (customer_id, key, value, source booked|stated)
- `segments` (name, definition) · `customer_scores` (customer_id, rfm, lead_score, computed_at)

## Conversations & inquiries
- `conversations` (customer_id, channel, external_thread_id, status, last_inbound_at) — drives the WhatsApp 24h window
- `messages` (conversation_id, direction, channel, body(enc), media_refs, template_id, status, external_id UNIQUE per channel, approval_id)
- `inquiries` (customer_id, conversation_id, source_id, state, trip_type, urgency, assigned_user_id, lost_reason)
- `requirements` (inquiry_id, version, data JSONB (schema-validated), field_confidence JSONB, source_spans JSONB, missing_fields)
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
- `approval_requests` (action_type, subject_type/id, proposed JSONB, context JSONB, policy_level, state pending|approved|edited|rejected|expired, reviewer_id, decided_at, reason, final JSONB, diff JSONB, expires_at, escalated_at)

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
