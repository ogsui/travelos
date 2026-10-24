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
