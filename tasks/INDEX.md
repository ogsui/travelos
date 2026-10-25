# Task index

Run in order. `/task NNN` in OpenCode. Review, commit, then start a fresh session for the next task.

| # | Task | Phase | Depends on |
|---|---|---|---|
| 001 | Repo scaffold & dev environment | 1 | — |
| 002 | Core database, money type, encryption | 1 | 001 |
| 003 | Auth, RBAC, audit service, kill switches | 1 | 002 |
| 004 | Workflow engine, timers, outbox | 1 | 002, 003 |
| 005 | Approval engine & autonomy policies | 1 | 003, 004 |
| 006 | LLM gateway, redaction, prompt registry | 1 | 001, 003 |
| 007 | Synthetic data generators | 1 | 002 |
| 008 | CRM core (umbrella; see sub-tasks) | 2 | 002, 003, 007 |
| 008a | Consent service and API | 2 | 008 |
| 008b | CRM customer/contact/traveller records and API | 2 | 008a |
| 008c | Keyed contact fingerprints and seed-hash migration | 2 | 008b |
| 008d | CSV/Excel importer | 2 | 008c |
| 008e | Approval-gated reversible customer merge | 2 | 008b, 008d |
| 009 | Channel adapters and unified CRM timeline | 2 | 004, 008e |
| 010 | Inquiry parser & missing-info loop | 2 | 006, 007, 008, 009 |
| 011 | Sales conversation agent | 2 | 005, 006, 010 |
| 012 | Supplier adapters & price snapshots | 2 | 004, 007 |
| 013 | Itinerary & package builder | 2 | 010, 012 |
| 014 | Competitor price adapters | 2 | 007, 012 |
| 015 | Deterministic pricing engine | 2 | 012, 013, 014 |
| 016 | Quotes, PDFs, acceptance, follow-ups | 2 | 005, 009, 015 |
| 017 | Approval & inbox dashboard (mobile-first) | 2 | 003, 005, 016 |
| 018 | Eval harness & shadow comparator | 2 | 010, 013, 015, 016 |
| 019 | Payments & reminders | 3 | 004, 005, 016 |
| 020 | Booking orchestration | 3 | 005, 012, 019 |
| 021 | Billing, invoices, ledger | 3 | 019, 020 |
| 022 | Travel documents & customer portal | 3 | 020, 021 |
| 023 | Support agent & disruption handling | 3 | 006, 009, 020, 005 |
| 024 | Post-trip, reviews, referrals | 3 | 008, 009, 022 |
| 025 | CRM intelligence & segments | 4 | 008, 021 |
| 026 | Campaigns & customer outreach | 4 | 005, 008, 009, 025 |
| 027 | Content calendar & generation | 4 | 005, 006, 012 |
| 028 | Social publishing adapters | 4 | 027, 005 |
| 029 | Public website & landing pages | 4 | 009, 027 |
| 030 | SEO content with fact-checking | 4 | 027, 029 |
| 031 | Paid ads manager | 4 | 027, 005 |
| 032 | Instagram/Facebook/GBP/phone lead capture | 4 | 009, 008 |
| 033 | Analytics marts & dashboards | 5 | 021, 026, 031 |
| 034 | Digests, alerts, recommendations | 5 | 033, 006 |
| 035 | Manager: SLAs, routing, graduation | 5 | 004, 005, 033 |
| 036 | Excel/Tally exports & reconciliation | 5 | 021 |
| 037 | Observability, backups, deployment | 5 | 001 |
| 038 | Security hardening & privacy workflows | 5 | 003, 008, 037 |
| 039 | 30-day shadow run tooling | 6 | 018, 038 |
| 040 | Pilot: enable live mode safely | 7 | 039 |

## Milestones
- **After 018 — DEMO**: replay real-looking inquiries end to end on synthetic data (inquiry -> quote PDF -> approval).
- **After 024 — Transactions complete** on mocks/sandboxes.
- **After 032 — Growth engine complete** (every outbound channel behind a kill switch).
- **After 038 — Ready for shadow.**
- **039 — Shadow (30 days)**, **040 — Pilot (live at A1).**
