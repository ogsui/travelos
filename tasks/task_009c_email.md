# Task 009c — Email channel adapters

Phase 2 · Depends on: 009a · Part of: 009

Implement the email adapter on the 009a channel contract. Use Mailpit in development and provider interfaces for inbound/outbound transport. Do not invent Gmail/SES/SMTP credentials or provider-specific behavior absent owner-supplied documentation. Enforce consent/suppression, approval, channel kill switch and mode gates on sends; inbound events must be deduplicated and privacy-safe. Add fixture-based contract tests and update docs. Commit `task-009c: ...`.
