# Task 024 — Post-trip, reviews, referrals

Phase 3 · Depends on: 008, 009, 022 · Requirements: POST-1, POST-2, POST-3, POST-4

## Read first
- AGENTS.md
- docs/05_APPROVAL_MATRIX.md
- docs/07_SECURITY_COMPLIANCE.md

## Needs from owner
- Review link (Google Business Profile), reward policy.

## Scope
- Feedback/review requests, photo/testimonial consent workflow, referral codes and reward ledger, occasion messages.
- All sends consent-checked and template-based.

## Acceptance criteria
- Testimonial cannot be created without consent_ref (DB constraint + test).
- Rewards require approval.
- Messages respect caps and windows.

## Human gates / approvals
- Publishing testimonials LOCKED A1; review requests A2.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-024: ...`, write the report from AGENTS.md, then STOP.
