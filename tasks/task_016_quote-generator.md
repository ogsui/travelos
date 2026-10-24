# Task 016 — Quotes, PDFs, acceptance, follow-ups

Phase 2 · Depends on: 005, 009, 015 · Requirements: QUO-1, QUO-2, QUO-3, SALES-6

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md
- docs/05_APPROVAL_MATRIX.md
- docs/06_PRICING_ENGINE.md

## Needs from owner
- Brand assets (logo, colours), T&Cs text, sample past quotes.

## Scope
- Quote + revision models, expiry, diff-from-previous.
- Branded multi-option PDF (WeasyPrint) and web view; fare rules (refundability) prominent; visa notes 'to be confirmed' unless from the source.
- Send via channel adapters after approval; delivered/viewed/accepted tracking; accept link with token.
- Re-quote flow on plan changes; follow-up cadence for open quotes (templates, consent-aware, stops on reply/accept).

## Acceptance criteria
- Revision diff correct (test).
- Quote totals equal the stored price calculation exactly.
- Expired quotes can't be accepted.
- Follow-ups stop on accept/reply/STOP.

## Human gates / approvals
- Send quote A1; below-floor quotes cannot reach this stage without owner approval.

## Finish
Tests first, `make check` green (+ `make eval` for LLM parts), docs updated, commit `task-016: ...`, write the report from AGENTS.md, then STOP.
