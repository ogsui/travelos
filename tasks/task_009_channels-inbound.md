# Task 009 — Channel adapters: WhatsApp, email, web form, mock

Phase 2 · Depends on: 004, 008 · Requirements: CH-1, CH-2, CH-4, CH-5

## Read first
- AGENTS.md
- docs/04_INTEGRATIONS.md
- docs/07_SECURITY_COMPLIANCE.md
- docs/03_DATABASE.md

## Needs from owner
- WhatsApp Business Cloud API credentials/docs when available; mailbox access details for email.

## Scope
- Message/Conversation models; `ChannelAdapter` interface (receive, send, status, templates).
- WhatsApp Cloud API: webhook with signature verification, dedupe, media, 24h-window logic, template send path.
- Email: IMAP/Gmail inbound + SMTP/SES outbound adapters; Mailpit in dev.
- Web-form endpoint with spam protection; Mock channel writing to a local outbox folder.
- Conversation threading; consent + suppression check on every send; STOP/unsubscribe keyword handling.

## Acceptance criteria
- Replayed/duplicate webhooks are idempotent.
- Send without consent or outside window without template is blocked (tests).
- Contract tests pass against Mock; real adapters tested with recorded fixtures.
- Nothing sends in `test` or `shadow` mode.

## Human gates / approvals
- Outbound sends go through the ApprovalEngine per the matrix; channel kill switches default OFF.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-009: ...`, write the report from AGENTS.md, then STOP.
