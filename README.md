# TravelOS — build pack for OpenCode

An AI-assisted operating system for a travel agency: lead capture, WhatsApp/email conversations, supplier + competitor price comparison, deterministic pricing, quotes, approvals, payments, booking, GST billing, travel documents, support, post-trip loyalty, outreach, social/SEO/ads, analytics. Built for real use; developed and tested on synthetic data; rolled out through shadow mode then a narrow pilot.

This pack is the **blueprint and task list**, not finished code. OpenCode builds the system one small task at a time from `AGENTS.md`, `docs/` and `tasks/`.

## Start
1. Open a terminal in this folder and run `opencode`. `AGENTS.md` and `opencode.json` load automatically.
2. Choose a model with `/models`. Use the strongest model you have for tasks 004-006, 012, 015, 020, 021 (state machines, LLM gateway, suppliers, pricing, booking, billing). Free models are fine for UI and docs-heavy tasks if you review carefully.
3. **Free-tier models may train on your prompts. Only synthetic data, never real customer data, keys or `.env` contents.**
4. `git init && git add -A && git commit -m "spec pack"`.
5. `/task 001` -> read the report -> `git diff` -> `make check` -> commit -> start a NEW session -> `/task 002`. Use `/review` after each task and `/status` any time.

## Demo path
Tasks 001-018 end with a replay tool that takes an inquiry through parsing, supplier simulation, packaging, pricing, branded PDF quote and approval screen. That is the demo for the owner. Real TBO/Riya adapters come after the owner buys API access (sub-tasks 12b/12c).

## Do these now (non-code, they block later tasks)
- [ ] Interview the owner and accountant with `docs/11_BUSINESS_RULES_INTERVIEW.md`; fill `config/business_rules.yaml` (copy from the example).
- [ ] Time one real quote end to end and collect 20 anonymised past quotes + 10 invoices + the master Excel/tax workbook layout.
- [ ] Ask TBO/Riya the questions in `docs/04_INTEGRATIONS.md`; request sandbox access.
- [ ] Start Meta Business verification + WhatsApp Business number; list social accounts and who owns the logins.
- [ ] Pick a payment gateway; start the account.
- [ ] Find out where the existing customer list lives and what consent evidence exists.
- [ ] Choose an LLM provider for real data with a no-training agreement (before shadow mode).

## Where I used human approval
See `docs/05_APPROVAL_MATRIX.md`. Always human: moving money, booking/ticketing confirmation, cancellations/refunds, below-floor prices, new campaign audiences, ad budget increases, testimonials, tax filing, data deletion. Most everything else starts as "AI drafts, human approves" and earns autonomy from measured results.

## Layout
```
AGENTS.md  opencode.json  README.md
.opencode/commands/   /task /review /status
docs/    00 vision · 01 requirements · 02 architecture · 03 database · 04 integrations
         05 approval matrix · 06 pricing · 07 security/compliance · 08 testing/rollout
         09 coding standards · 10 roadmap · 11 business-rules interview · DECISIONS · BACKLOG
tasks/   INDEX + 40 task files
config/  business_rules.example.yaml
prompts/ (filled by tasks)
```
