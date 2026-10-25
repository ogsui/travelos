# Task 009a — Channel foundation, mock inbound, and CRM timeline

Phase 2 · Depends on: 004, 008e2b · Part of: 009

## Read first
- AGENTS.md
- docs/02_ARCHITECTURE.md
- docs/03_DATABASE.md
- docs/04_INTEGRATIONS.md
- docs/07_SECURITY_COMPLIANCE.md

## Scope
- Add tenant-scoped `Conversation` and `Message` persistence with encrypted message bodies, normalized channel/direction/timestamp fields, optional CRM customer/contact links, provider thread/message identifiers, and dedupe constraints.
- Add a deterministic channel-adapter protocol and a mock inbound fixture adapter; it must persist inbound events idempotently and never send in test or shadow mode.
- Add an authenticated CRM timeline query, newest first, tenant-scoped, with optional customer/contact/channel filters and bounded result size.
- Write audit metadata for inbound message acceptance without copying message body or contact details into the audit log.
- Add migration and update database/testing/architecture docs.

## Out of scope
- Public web form intake (009b), email transport (009c), WhatsApp provider integration (009d), outbound delivery/approval flow, media storage, and campaign messaging.

## Acceptance criteria
- Duplicate `(channel, external_id)` inbound events do not create duplicate messages.
- Conversation threading is stable per tenant/channel/provider thread ID.
- Timeline is newest-first, tenant-scoped, and can be filtered to a CRM customer/contact.
- Stored message bodies are encrypted at rest; audit rows contain no body text.
- Mock outbound send is blocked in test and shadow modes.
- Route access registry has allow/deny coverage.

## Finish
Tests first, run `make check` (or direct equivalents on Windows), docs updated, commit `task-009a: ...`, report and stop.
