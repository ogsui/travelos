# Task 016 — Quotes, PDFs, acceptance, follow-ups

Phase 2 · Depends on: 005, 009, 015 · Requirements: QUO-1, QUO-2, QUO-3, SALES-6

## Read first
- AGENTS.md
- docs/01_REQUIREMENTS.md
- docs/05_APPROVAL_MATRIX.md
- docs/06_PRICING_ENGINE.md

## Needs from owner
- Brand assets (logo, colours), T&Cs text, sample past quotes.
- Owner-approved follow-up templates (016d) and an owner decision on multi-option quoting.

## Split (owner-approved 2026-11-10)
The umbrella task exceeds the ~800-line review budget, so it is delivered in the slices below, one commit each, in this order.

| Slice | Scope | State |
|---|---|---|
| **016a** | Quote/revision persistence: `quotes`, `quote_revisions`, `quote_lines` (migration 0018), exact copy of an `ok` calculation, revision numbering, expiry, diff, draft-only revisions, pure acceptance predicate | **done** |
| **016b1** | Staff-labelled, append-only multi-option groups (`quote_groups`, `quote_group_options`, migration 0019), ordered labels, same-inquiry/one-group rules. No tier ranking is inferred | **done** |
| **016b2a** | Fail-closed owner presentation config (`quote_presentation`) and pure escaped HTML rendering: total + tax lines only, fare rules/refundability, visa notes "to be confirmed", payment schedule, snapshot refs. Reports `NEEDS_CONFIG` rather than invent brand, legal or payment text | **done** |
| **016b2b** | DB document builder over one quote or a staff-labelled group, lazy WeasyPrint PDF adapter (`[pdf]` extra), `quote.rendered` audit and integration tests. No document is persisted; production output still needs the owner's real brand assets, T&Cs and payment schedule | **done** |
| **016c1** | `quote_events` (migration 0020) with a per-quote position, hashed accept links (rotating, not single-use), view tracking, and acceptance refusing expired/stale/already-accepted/not-sent quotes | **done** |
| **016c2** | Approval-gated send (`quote.send` A1), consent/mode/kill-switch gates, public link endpoints and delivery, owner-approved below-floor entry | pending |
| **016d** | Re-quote flow on plan changes with a reviewer diff, owner-approved follow-up templates, consent-aware cadence, stop on accept/reply/STOP, timer/outbox wiring | pending |

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
