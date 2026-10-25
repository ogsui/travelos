# Data

Only obviously fake synthetic data belongs here. Real customer data, passports,
payment references and production exports must never be committed or copied
into this repository.

- `synthetic/` — committed, generated datasets (task 007).
- `schemas/` — JSON Schemas every generated record must validate against.
- `eval/` — gold sets and adversarial fixtures (tasks 006, 007 and 018).

Generators are deterministic and seeded so tests and evals are reproducible.
`make seed` (or `python -m travelos.admin.seed`) regenerates `synthetic/` and
`eval/adversarial_messages.jsonl` with `SYNTHETIC_SEED` (default `20261025`),
then loads the CRM portion into PostgreSQL. The same seed produces byte-
identical files; changing the seed is a deliberate dataset change. If the
database is unavailable the command still writes the files and logs a warning.

## Files

Each `.jsonl` file has one JSON object per line. `manifest.json` records the
generator version, seed, record count and SHA-256 for every dataset file.
Tests fail if a committed file is edited by hand or no longer matches its
schema.

| File | Records | Schema | Contents |
|---|---:|---|---|
| `synthetic/customers.jsonl` | 500 | `customer.schema.json` | Customers, contacts, consent history, travellers, past bookings |
| `synthetic/inquiries.jsonl` | 160 | `inquiry.schema.json` | 10 per category with gold requirements, missing fields and expected signals |
| `synthetic/quotes_gold.jsonl` | 40 | `quote.schema.json` | Hand-checkable integer-paise quotes linked to inquiries |
| `synthetic/supplier_inventory.jsonl` | 55 | `supplier_inventory.schema.json` | Mock supplier SKUs with price drift and sold-out points |
| `synthetic/supplier_scenarios.jsonl` | 30 | `supplier_scenario.schema.json` | Price change, sold out, timeout, partial failure, hold expiry |
| `synthetic/competitor_snapshots.jsonl` | 30 | `competitor_snapshot.schema.json` | OK, stale, blocked and unavailable public-page snapshots |
| `synthetic/channel_webhooks.jsonl` | 30 | `channel_webhook.schema.json` | Inbound channel fixtures incl. dedupe, out-of-order, media and STOP |
| `synthetic/payments.jsonl` | 24 | `payment.schema.json` | Full, partial, failed, duplicate, late and refund webhooks |
| `synthetic/marketing_history.jsonl` | 30 | `marketing_history.schema.json` | Social posts, ad metrics and SEO keyword rows |
| `eval/adversarial_messages.jsonl` | 18 | `adversarial_message.schema.json` | Injection, abuse, PII fishing, refund pressure, giant messages |

Inquiries cover domestic and international flights, family packages, honeymoon,
pilgrimage, corporate multi-city, groups/MICE, visa-only, hotel-only, vague,
contradictory, Hinglish, Hindi, mid-conversation date changes, misspelt names
and long layovers. Family cases include infants and child ages. Hinglish and
Hindi text is included verbatim; it is fixture data, not advice.

## Reserved fake patterns

- E-mail: `*@example.test` only (the reserved test domain).
- Phone: `+91 00000 <five digits>` only (non-routable / invalid).
- Passport: `F7` followed by six digits only.
- Names end in `Synthetic`, `Example`, `Sample`, `Fixture`, `Demo`, `Mock`,
  `Placeholder` or `Test`.
- Every timestamp is fixed and UTC (`...Z`); no generator reads the clock.

Money is integer paise with an explicit INR currency. Gold quotes use
`tax_paise = 0` because accountant-approved tax tables do not exist yet
(task 015); no tax rule is implied.

## Database seeding

At task 007 only CRM tables exist, so the database seed loads `customers`,
`contacts`, `consents`, `suppression_list` and `travellers`. IDs are UUIDv5
values derived from the seed and the fixture natural key, making a re-run an
idempotent no-op. PII columns stay encrypted at rest through the existing
column types. Inquiry, quote, supplier and payment tables arrive in later tasks
and keep consuming the same JSONL files.
