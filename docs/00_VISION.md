# 00 Vision

## What
TravelOS is an AI-assisted operating system for an established Indian travel agency. It runs the repetitive ~80% of the work (lead capture, follow-ups, price comparison, quoting, paperwork, content, reporting) so people can focus on decisions that involve money, commitments and relationships.

## Who uses it
- **Owner** — approver of last resort; sets business rules and autonomy levels.
- **Sales agent(s)** — day-to-day approvals, customer relationships, exceptions.
- **Accountant** — approves billing/tax outputs, receives exports.
- **Admin (developer)** — configuration, integrations, monitoring.
- **Customers** — talk to the agency on WhatsApp/email/web; see quotes, documents and status.

## Principles
1. Humans approve commitments; AI prepares and drafts.
2. Deterministic code for money; LLMs only for language and fuzzy parsing.
3. Autonomy is earned per action type (levels A0–A3), measured, and reversible.
4. Build for production, test on synthetic data, run in shadow mode, then pilot with real samples.
5. Official APIs first. Fragile integrations (scraping, portal automation) are optional, switchable and never block the core flow.
6. Consent-first marketing; nothing is sent to people who have not agreed to hear from the agency.
7. Everything is auditable. Every irreversible step has a human gate or an undo window.
8. Boring and reliable beats clever.

## Non-goals (for now)
- AI moving money (supplier payments, refunds, payouts).
- Multi-agency SaaS (data model is tenant-ready; no tenant features are built).
- Replacing staff or the owner's judgement.
- Autonomous visa, legal or tax advice.

## Success metrics (targets confirmed during shadow mode)
| Metric | Target |
|---|---|
| First response to a new inquiry | < 2 min, 24x7 (acknowledgement) |
| Draft quote ready for approval | < 15 min for standard trips |
| Staff time per quote | -60% vs baseline measured in shadow mode |
| Required-field extraction accuracy | >= 95%, zero silent assumptions |
| Price/tax errors reaching a customer | 0 |
| Open quotes with scheduled follow-up | 100% |
| Invoices generated same day as booking | 100% |
| Repeat-customer reactivation (consented list) | measured monthly |

## Operating loop
Inquiry -> understand -> search -> price -> approve -> quote -> accept -> pay (human-verified) -> book (human-confirmed) -> invoice -> documents -> support -> post-trip -> referral/repeat. Marketing and outreach feed the top of the loop; analytics and the orchestrator watch all of it.
