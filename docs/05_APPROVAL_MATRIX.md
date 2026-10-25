# 05 Approval Matrix & Autonomy

## Levels
- **A0 Human does it.** System may prepare data only.
- **A1 AI drafts, human approves every instance.**
- **A2 AI acts, human reviews after the fact** (sample or all) with an undo/hold window where possible.
- **A3 AI acts autonomously**, monitored by metrics and alerts.

Levels live in `autonomy_policies` and are enforced by the ActionPolicy layer. Defaults below. Rows marked **LOCKED** cannot be raised by configuration.

## Defaults
| Action | Start | May graduate to | Notes |
|---|---|---|---|
| Capture lead, create inquiry, update CRM | A3 | — | Internal only |
| Bulk CRM import | A1 | — | **LOCKED**; an agent or owner must approve every batch before records are written |
| Customer merge | A1 | — | **LOCKED**; an agent or owner must approve each exact merge snapshot |
| Parse inquiry into requirement | A3 | — | Result shown to reviewers |
| Instant acknowledgement (approved template) | A1 in shadow, A3 live | — | Template text approved by owner |
| Clarifying questions to customer | A1 | A2 | After >= 95% approved-unchanged over 100 cases |
| Free-form sales reply | A1 | A2 for FAQ-type only | Price/availability promises are never free-form |
| Discount/negotiation within rules | A1 | — | Below floor: owner only |
| Supplier search, package build | A3 | — | Internal |
| Competitor price collection | A3 | — | Per-site flag; internal |
| Price calculation | A3 (deterministic) | — | Output reviewed via quote approval |
| Quote below profit floor / floor above ceiling | A0 (owner) | — | **LOCKED** |
| Send quote | A1 | A2 for simple products under value threshold | Owner decision after graduation criteria |
| Follow-up messages on open quotes | A1 | A2 | Approved cadence templates |
| Create payment link | A1 | A2 | |
| Record payment from signed webhook | A3 | — | Reconciled daily |
| Supplier payment / wallet funding | A0 | — | **LOCKED** |
| Fare revalidation | A3 | — | Blocks on tolerance breach |
| Booking / ticketing confirmation | A1 | — | **LOCKED** at A1 minimum |
| Cancel / amend / refund execution | A1 | — | **LOCKED**; AI computes charges, human executes |
| Invoice/receipt/voucher generation | A1 (accountant) | A2 after template sign-off | Gapless numbering enforced in code |
| Tax filing / return submission | A0 | — | **LOCKED**; AI prepares export only |
| Update master Excel / ledger | A2 | A3 | Daily reconciliation |
| Send travel documents | A2 | — | Automatic consistency check first; hold window |
| Visa guidance to customer | A1 | — | Only from authoritative source, cited |
| Disruption handling | A1 (urgent) | — | Pages staff immediately |
| Support FAQ answers (booking data/KB) | A1 | A2 | |
| Complaints, disputes, refund requests | A0/A1 | — | Human leads |
| Review/feedback request (consented) | A2 | A3 | |
| Publish a testimonial | A1 | — | **LOCKED**; consent_ref required |
| Birthday/anniversary/next-trip message (consented) | A1 | A2 | |
| Referral reward credit | A1 | — | |
| Campaign: audience definition | A1 | — | **LOCKED** for new audiences |
| Campaign: sends inside approved envelope | A3 | — | Caps, windows, suppression enforced in code |
| Cold outreach to non-consented contacts | Disabled | — | **LOCKED** OFF; owner enable per list with legal basis |
| Social post: evergreen, no price/offer | A1 | A2 | |
| Social post: price/offer/claim | A1 | — | Price accuracy check mandatory |
| Reply to comments/DMs | A1 | A2 for simple thanks/FAQ | |
| SEO article/page publish | A1 | — | Fact-check + sources required |
| Ad campaign create/edit | A1 | — | |
| Ad budget increase | A0/A1 (owner) | — | **LOCKED**; hard caps in code |
| Ad auto-pause on anomaly | A3 | — | Safety action only |
| Analytics reports/digests | A3 | — | |
| Apply an analytics recommendation | A1 | — | |
| Data export/deletion for a customer | A1 | — | **LOCKED** deletion needs owner |
| Change autonomy levels / business rules | A0 (owner) | — | **LOCKED** |
| Change a kill switch / feature flag | A0 (owner) | — | **LOCKED**; owner-only endpoint, audited before/after |

## Graduation rules
- Per action type, the tracker records proposals, approved-unchanged, edited, rejected, incidents (wrong outcome reached a customer or cost money).
- Suggest promotion only when: >= 100 instances, >= 95% approved-unchanged, 0 incidents in the window, owner explicitly approves the change.
- Auto-demote one level (and alert) if incidents > 0 in a 30-day window or approved-unchanged < 85% over the last 50.
- Promotions are per product class (e.g. domestic flights before international packages).

## Approval UX requirements
- Mobile-first; one-tap approve for simple items; side-by-side original message vs extracted requirement; fields the AI was unsure about highlighted; price breakdown with cost, margin, taxes; fare rules (refundable?) shown prominently; what changed since last revision; edit-in-place; mandatory reason on reject.
- Expiry and escalation: unanswered requests escalate (agent -> owner) after configurable timers; customers get a holding message from approved templates.
- Four-eyes (second approver) above the configured value threshold and for LOCKED financial actions.
- Every decision is audited with before/after.

## Implementation notes (task 005)
- The enforceable catalogue is `src/travelos/approval/matrix.py`. It encodes every row above, including `default_level`, `max_level`, `locked`, `owner_only`, four-eyes and notes. `docs/05_APPROVAL_MATRIX.md` remains the human source of truth; a change here must be mirrored there (and the catalogue test counts the LOCKED set).
- Resolution: A0 -> `deny`; A1 -> `needs_approval`; A2/A3 -> `auto` (A2 carries `review_after`). Stored levels are clamped to `max_level`; LOCKED actions always resolve to their fixed default. Unknown action types fail closed.
- Four-eyes is active when the catalogue flags it, the owner sets `requires_four_eyes`, or the instance value reaches the owner's `value_threshold_paise` (threshold `0` disables the value trigger). A four-eyes requirement downgrades an otherwise automatic decision to `needs_approval`, because two humans cannot be replaced by a machine.
- Owner-only changes go through `PUT /autonomy/policies/{action_type}` (`autonomy:write`, owner role only) and `AutonomyService.update_policy`. LOCKED actions and levels above the ceiling are rejected with `policy_locked` before any row is written, and every change is audited with before/after.
- Demotion is implemented as a daily worker hook. Promotion remains a human decision: the tracker records the data, the owner approves a promotion through the same endpoint, and there is no automatic raise. The matrix's graduation criteria are not wired into code yet (task 037/graduation work).
- Approval requests carry a proposed-versus-final diff in the audit log (`approval.decided`); the table keeps both payloads. Escalation and expiry create owner `human_tasks` and emit events, so no unanswered request disappears.
- `docs/03_DATABASE.md` records the table columns; `docs/08_TESTING_EVAL.md` lists the tests that enforce these rules.
