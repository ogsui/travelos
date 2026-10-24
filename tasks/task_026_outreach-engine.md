# Task 026 — Campaigns & customer outreach

Phase 4 · Depends on: 005, 008, 009, 025 · Requirements: OUT-1, OUT-2, OUT-3, OUT-4, OUT-5

## Read first
- AGENTS.md
- docs/05_APPROVAL_MATRIX.md
- docs/07_SECURITY_COMPLIANCE.md
- docs/04_INTEGRATIONS.md

## Needs from owner
- Approved WhatsApp templates; email domain authentication; DLT details if SMS.

## Scope
- Campaign model, audience builder with consent/DND/suppression filters, template manager, A/B tests.
- Personalization via approved template slots filled from CRM; LLM only drafts variants for human approval.
- Frequency caps, send windows, auto-pause on quality/complaint/bounce spikes; global STOP.
- Cold-outreach module present but OFF (LOCKED); enabling needs owner + legal basis record per list.

## Acceptance criteria
- Non-consented contact can never be targeted (tests).
- Caps/windows enforced in code.
- Envelope approval: individual sends only inside approved audience+content.

## Human gates / approvals
- New audience approval is LOCKED; sends inside the envelope A3.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-026: ...`, write the report from AGENTS.md, then STOP.
