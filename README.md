# TravelOS — build pack for OpenCode

An AI-assisted operating system for a travel agency: lead capture, WhatsApp/email conversations, supplier + competitor price comparison, deterministic pricing, quotes, approvals, payments, booking, GST billing, travel documents, support, post-trip loyalty, outreach, social/SEO/ads, analytics. Built for real use; developed and tested on synthetic data; rolled out through shadow mode then a narrow pilot.

This pack is the **blueprint and task list**, not finished code. OpenCode builds the system one small task at a time from `AGENTS.md`, `docs/` and `tasks/`.

## Current status
- **Tasks 001–010 complete** — foundation, safety gates, workflow and approval engines, LLM gateway, synthetic data, CRM (consent, records, import, merge), channels, and the inquiry parser with its hand-checked gold set and eval runner.
- **Task 011 in progress** — 011a is complete: owner-approved acknowledgement templates, a deterministic reply claim guard, and approval-gated encrypted drafts. 011b (handoff triggers and conversation summaries) and 011c (sales-reply eval rubric) are next.
- See `git log` for the per-task history and `tasks/INDEX.md` for the full plan.

## Quickstart (Docker)
Needs Docker Desktop with the WSL 2 backend. `docker compose` is sufficient; `make` is optional.
```sh
cp .env.example .env
# Required even in test mode: customer PII is encrypted at rest. Generate once
# and keep it — changing the key makes existing ciphertext unreadable.
python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"
# paste the result into .env as ENCRYPTION_KEY=...

make up        # or: docker compose up -d --build
# Seed inside the container so no host Python/venv is needed:
docker compose run --rm api python -m travelos.admin.seed
curl http://localhost:8000/health
curl http://localhost:8000/ready
# dashboard http://localhost:3000 · mailpit http://localhost:8025 · minio http://localhost:9001
```
`docker compose up` runs a one-shot `migrate` service (`alembic upgrade head`) before the API starts, so a fresh Postgres volume is upgraded automatically. Seeding is still a separate step; without it the database has no policies or synthetic CRM rows. `make seed` does the same from a host venv.

Two caveats: `/ready` only reports that configuration loads (it does not probe Postgres or Redis yet), and the project has no bootstrap-admin command, so there is no `users` row to log in with. Both are tracked in `docs/BACKLOG.md`.

Reset everything including the volumes with `docker compose down -v`, then `docker compose up -d --build` and re-seed.

`make check` runs lint + `mypy --strict` + tests. `make eval-inquiry` refreshes the parser eval result (fake plumbing mode). `make logs` follows the stack; `make down` stops it.

## Quickstart (no Docker, Windows or Linux)
Python 3.12 is enough for the checks; Node 22+ is only needed for the dashboard.
```sh
python -m venv .venv
# Windows:
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python -m pytest
.venv\Scripts\python -m travelos.inquiry.eval --mode fake
.venv\Scripts\ruff check .
.venv\Scripts\mypy src
# Linux/macOS: source .venv/bin/activate, then pip install -e ".[dev]" and make check
```
On Windows without GNU Make, run the underlying commands directly (`python -m pytest`, `ruff`, `mypy`) or install Make. `make up` still requires Docker Desktop.

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
