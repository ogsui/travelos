# 07 Security & Compliance

> Engineering requirements, not legal advice. Have a lawyer/CA confirm the legal items below before going live; regulations and commencement dates change.

## Data classification
| Class | Examples | Rules |
|---|---|---|
| Restricted | passport no., DOB, payment references, documents/scans | Field-level encryption, never in prompts/logs, minimal access, retention limit |
| Personal | names, phones, emails, itineraries, messages | Encrypted at rest, redacted in logs, only `no_training` providers when sent to an LLM |
| Business | prices, margins, supplier terms | RBAC, audit |
| Public | blog/site content | — |
| Synthetic | everything in `data/` | The only data allowed with free-tier/training providers |

## Application security
- RBAC per endpoint; owner/accountant/agent/admin; least privilege; MFA for owner/admin.
- Argon2 password hashing, session hardening, CSRF protection, CORS allowlist, rate limiting, secure headers.
- Webhooks: signature verification, replay protection (timestamp + idempotency), IP allowlists where offered.
- Secrets in environment/secret manager; rotation procedure; secret scanning in CI; `.env` never committed or read by agents.
- Contact dedupe and suppression use normalized, channel-scoped HMAC-SHA256 fingerprints. The fingerprint key is domain-separated from the field-encryption key; rotating the root key requires a coordinated migration of encrypted fields and all fingerprints. Plaintext contact values never enter digest columns or logs.
- Dependency scanning and pinned versions; container images minimal and non-root.
- Backups: encrypted, daily, restore-tested monthly. RPO/RTO written down.
- Admin actions and exports audited; audit log tamper-resistant.

## LLM-specific security
- **Prompt injection**: all customer/email/web/supplier text is untrusted data, delimited, never in system prompts. The model returns proposals; code decides. No tool access from inbound text. URLs from messages are never fetched without an allowlist.
- **Data leakage**: redact PII before calls (placeholders); data-class vs provider-policy enforcement; no real data to free-tier providers; `llm_calls` stores metadata, token/cost counts and redacted text only, never the reversible placeholder mapping. Redacted rows are purged after 90 days (`LLM_CALL_RETENTION_DAYS`) by an audited maintenance task; the purge cannot be widened to UPDATE/TRUNCATE.
- **Hallucination controls**: schema validation; facts (prices, availability, policies, visa) come only from system data or approved KB, else "to be confirmed by staff".
- **Abuse**: per-contact rate limits, length limits, attachment scanning.
- Public web-form intake uses strict field-size validation, a honeypot, and a 10-minute source rate limit (5 accepted submissions). Shadow/live use an atomic Redis counter keyed by a keyed digest of the direct client address; test mode uses an in-memory limiter. Forwarded IP headers are not trusted. A Redis failure rejects intake closed. Form name/email/phone claims are not stored or linked to CRM identity until a verification flow exists; no consent is inferred from form submission.
- Red-team suite `tests/eval/test_llm_injection.py` runs in CI against `data/eval/llm_injection.jsonl` (see `08_TESTING_EVAL.md`).

## Privacy (India: verify with counsel)
- Digital Personal Data Protection Act 2023 and Rules: notice, purpose-limited consent, withdrawal as easy as giving, data-principal rights (access/correction/erasure), breach handling, special care for children's data (many travellers are minors: consent via parent/guardian), retention limits, processors/vendors agreements.
- Consent records are first-class (`consents`), with proof and source. Marketing consent is separate from transactional.
- Data retention schedule (configurable): quotes, bookings/invoices (tax/accounting retention), messages, passport data (delete after travel + legal retention), analytics.
- Customer export/delete workflow (approval-gated) with audit.
- Breach runbook including reporting obligations (DPDP, CERT-In timelines — confirm current requirements).

## Messaging & marketing compliance
- WhatsApp: opt-in required; approved templates outside the 24h window; honour quality rating; no purchased lists.
- SMS/voice: TRAI DLT registration and DND scrubbing; promotional vs transactional sender IDs.
- Email: unsubscribe link/header, sender authentication, suppression list.
- Ads/social: platform ad policies; ASCI advertising code; no misleading prices ("from" prices need conditions and dates); disclose AI-generated media where platforms require.
- Reviews/testimonials: genuine only; consent recorded; no incentives that violate platform/consumer rules.
- Cold outreach: OFF by default; legal basis recorded per list.

## Financial/tax controls
- GST/TCS logic from accountant-approved tables only; invoices numbered gaplessly per series; credit notes for corrections; no deletion of issued invoices (void + credit note).
- No card data stored (hosted payment pages only). Payment webhooks reconciled daily.
- Four-eyes on LOCKED financial actions above the threshold.

## Travel-industry controls
- Names exactly as per passport; validation of age vs fare type; supplier hold/ticketing deadlines tracked; fare-rule display (refundability) before acceptance; visa and health requirements are advisory "confirm with the embassy/official source" and human-verified.

## Operational security
- Kill switches for every outbound channel, scraper, ad account and social account.
- Incident runbooks: wrong message sent, wrong price quoted, double booking, leaked data, supplier outage, WhatsApp number restricted, payment mismatch.
- Access reviews quarterly; staff offboarding checklist.
