# Task 009b — Web-form inbound channel

Phase 2 · Depends on: 009a · Part of: 009

## Read first
- AGENTS.md
- docs/03_DATABASE.md
- docs/04_INTEGRATIONS.md
- docs/07_SECURITY_COMPLIANCE.md
- src/travelos/channels/service.py
- src/travelos/crm/fingerprints.py

Implement a public web-form inbound endpoint on the 009a message foundation. Add bounded input validation, honeypot and rate/spam protection that can be tested without provider/network access, idempotent submission handling, and privacy-safe audit metadata. Never trust a visitor's email/phone claim as identity: keep submissions unlinked to CRM records until a separate verification flow exists, and do not create contacts or consent from a form submission. Do not retain submitted name/email/phone in this slice. No outbound acknowledgement. Use synthetic tests and `MODE=test`; update integration/security/testing docs and commit `task-009b: ...`.
