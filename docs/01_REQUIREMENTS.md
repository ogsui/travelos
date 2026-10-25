# 01 Requirements

IDs are stable and referenced by tasks. Default autonomy levels (A0-A3) are defined in `05_APPROVAL_MATRIX.md`.

## CORE — Platform
- CORE-1 Modes `test | shadow | live`, per-channel/scraper/ad/social kill switches (default OFF); `live` refuses to start unless fully configured and confirmed.
- CORE-2 Roles: owner, agent, accountant, admin. RBAC on every endpoint. MFA-ready.
- CORE-3 Append-only audit log: state changes, approvals, outbound messages, price calculations, LLM calls, config changes.
- CORE-4 Workflow engine: explicit state machines, guarded transitions, timers (SLA, expiry, reminders), idempotent jobs, retries, dead-letter queue.
- CORE-5 Business rules in `config/business_rules.yaml` plus versioned DB overrides; every change audited.
- CORE-6 `tenant_id` on business tables (single tenant in practice).

## CRM — Customers & consent
- CRM-1 Customers, contacts (phone/email/WhatsApp), travellers (including children and infants), family links, preferences. Passport/DOB encrypted.
- CRM-2 Consent registry per contact x channel x purpose (source, timestamp, proof, withdrawn-at). Checked before EVERY outbound message. Unknown consent = no marketing.
- CRM-3 Import the agency's existing Excel/CSV customer and booking history with configurable column mappings, dedupe, a dry-run report, consent-status mapping and human approval before writes.
- CRM-4 Unified communication timeline across channels.
- CRM-5 Lifetime value, RFM segments, lead score, preferred destinations/airlines/hotels derived only from booked or stated facts.

## CH — Channels & lead capture
- CH-1 Inbound: WhatsApp Cloud API, email, website form/landing pages, Instagram DM, Facebook Messenger/lead ads, Google Business Profile messages, phone-call log entries. All normalize to Message/Conversation/Inquiry.
- CH-2 Webhook signature verification, idempotent processing, media handling, cross-channel dedupe (same person on multiple channels).
- CH-3 Source attribution (UTM, campaign, referrer, referral code) stored on every lead.
- CH-4 Outbound via the same adapters, gated by consent, templates, 24h-window rules, frequency caps and send windows.
- CH-5 STOP/unsubscribe handled instantly and globally.

## INQ — Inquiry understanding
- INQ-1 Parse English/Hindi/Hinglish messages into a validated Requirement (trip type, route(s), dates and flexibility, passengers by age, rooms, budget, preferences, nationality for visa, special needs, urgency).
- INQ-2 Detect missing/ambiguous required fields per trip type; ask the customer or flag staff; never assume silently.
- INQ-3 Per-field confidence and source span; show both next to the original message in the review screen.
- INQ-4 Match to an existing customer; merge duplicates only after locked A1 human approval, with a reversible audit trail.

## SALES — Conversation
- SALES-1 Instant acknowledgement from approved templates.
- SALES-2 Clarifying questions, destination/hotel/flight recommendations, objection handling, upsell suggestions (transfers, tours, upgrades; insurance only via a compliant flow or staff).
- SALES-3 Negotiation only inside owner-defined discount rules; never below the profit floor without owner approval.
- SALES-4 Handoff to a human on anger, complaint, large value, legal/visa/refund topics, repeated confusion.
- SALES-5 Conversation summaries and next-best-action suggestions for staff.
- SALES-6 Follow-up cadences for open quotes (templates, consent-aware, stop on reply/accept).

## SUP — Supplier search
- SUP-1 `SupplierAdapter` for flights, hotels, transfers, activities, visas: search, price, rules, hold, book, cancel, retrieve, wallet-balance (read-only).
- SUP-2 Adapters: Mock simulator, TBO, Riya (official APIs only, from supplied docs), optional PortalSessionAdapter (OFF by default).
- SUP-3 Immutable price snapshots with timestamp and TTL; fare rules and cancellation terms stored with each offer.
- SUP-4 Graceful degradation: partial supplier failure never blocks a quote; it is flagged.

## PKG — Package building
- PKG-1 Build itineraries from components (flights, hotels, transfers, activities, visas) for family, corporate, group and individual trips.
- PKG-2 Occupancy and age rules (infants, child ages, room types), multi-city routing, supplier deadlines.
- PKG-3 Produce up to three explainable option tiers (value / standard / premium).

## MKT — Market intelligence
- MKT-1 `CompetitorPriceAdapter`: ManualEntry, LicensedData (stub), PublicPageScraper for MakeMyTrip, Goibibo, Booking.com, Agoda, EaseMyTrip, Yatra (per-site flag, default OFF).
- MKT-2 Comparability score (route, dates, pax, fare class, baggage, refundability); only comparable, fresh snapshots influence the price ceiling.
- MKT-3 Scraper failures never block quoting; recorded as `unavailable`.
- MKT-4 Seasonality and demand signals from the agency's own history.

## PRC — Pricing
- PRC-1 Deterministic engine: minimum profitable price, recommended price, competitive ceiling, with a line-by-line explanation. Details in `06_PRICING_ENGINE.md`.
- PRC-2 Rules by product type, supplier, destination, customer segment, season, lead time, inventory.
- PRC-3 Anything below the floor, or floor above ceiling, is flagged and routed to the owner.
- PRC-4 Tax (GST/TCS) from an accountant-approved rule table, never from an LLM.

## QUO — Quotation
- QUO-1 Branded multi-option PDF + web view with revisions, expiry, supplier price snapshot reference, terms, payment schedule, visa notes ("to be confirmed" unless from the configured source).
- QUO-2 Send via WhatsApp/email after approval; track delivered/viewed/accepted; one-click accept link.
- QUO-3 Re-quote flow when the customer changes the plan; revision diff shown to the reviewer.

## APR — Approvals
- APR-1 Generic approval engine for any proposed action; stores the proposal, the diff against what was approved, reviewer, timestamp, reason.
- APR-2 Mobile-first review screen; approve / edit / reject / ask customer; bulk-approve only for categories marked safe.
- APR-3 Expiry and escalation timers; four-eyes above a value threshold.
- APR-4 Auto-demotion of an action's autonomy level when its error rate crosses a threshold.

## PAY — Payments
- PAY-1 Payment links via a gateway adapter (Razorpay/Cashfree/PayU), partial payments and instalment schedules.
- PAY-2 Webhooks (signature-verified) record payments; reconciliation job against gateway reports.
- PAY-3 Reminders from approved templates; refunds are recorded by a human after the human executes them.
- PAY-4 No card data ever touches the system.

## BKG — Booking
- BKG-1 Fare revalidation immediately before booking; block if price moved beyond tolerance; ask a human.
- BKG-2 Passenger-data validation (names as per passport, dates, ages vs fare type).
- BKG-3 Idempotent booking calls; partial-failure handling; hold/ticketing time limits tracked.
- BKG-4 Final confirmation by a human; booking references, tickets and vouchers stored against the booking.
- BKG-5 Supplier payment/wallet funding is human-only.

## BIL — Billing & accounting
- BIL-1 Proforma invoices, tax invoices, receipts, credit notes, vouchers with gapless numbering series.
- BIL-2 Tax computed from the accountant-approved table; accountant approves invoice templates and samples.
- BIL-3 Ledger entries per booking (customer receipts, supplier cost, margin, taxes).
- BIL-4 Automatic update of the agency's master Excel and tax workbook; exports for Tally/Zoho; monthly accountant pack.

## DOC — Travel documents & portal
- DOC-1 Itinerary PDF, ticket/voucher bundle, checklist, emergency contacts, weather and currency information.
- DOC-2 Consistency check of documents against the booking before sending.
- DOC-3 Customer portal via magic link; documents also pushed on WhatsApp/email.
- DOC-4 Visa information only from a configured authoritative source and verified by staff.

## SUPT — Support
- SUPT-1 24x7 answers on status, baggage, check-in, documents from booking data and the approved knowledge base.
- SUPT-2 Change/cancellation/refund requests become human-approved workflows with computed charges.
- SUPT-3 Disruption playbook (cancelled/delayed flights): urgent paging, option drafts, customer updates.
- SUPT-4 Complaint and sentiment detection with escalation.

## POST — Post-trip & loyalty
- POST-1 Feedback request, review request (Google Business Profile), photo/testimonial request with explicit consent.
- POST-2 Referral codes and reward ledger.
- POST-3 Birthday/anniversary and "next trip" messages to consented customers.
- POST-4 No fabricated testimonials; edits only with the customer's approval.

## OUT — Outreach & acquisition
- OUT-1 Campaign engine: audience builder from CRM with consent/DND filters, WhatsApp template manager, email, SMS (DLT), frequency caps, send windows, A/B tests.
- OUT-2 Priority audience: the agency's existing customer list (reactivation, seasonal offers, referrals).
- OUT-3 Each campaign: audience and content approved once; individual sends then run inside the approved envelope.
- OUT-4 Auto-pause on quality-rating drop, complaint spike or bounce spike.
- OUT-5 Cold outreach module exists but is OFF; enabling requires owner approval and a recorded legal basis per list.

## MKTG — Marketing content & social
- MKTG-1 Content calendar and idea engine from real offers, trends, festivals, visa news, seasonality.
- MKTG-2 Generate captions, hashtags, images (licensed/own photos preferred), short videos from templates, blog drafts, posting schedule.
- MKTG-3 Publish via official APIs to Instagram, Facebook, LinkedIn, X, Threads, YouTube Shorts; best-time scheduling from engagement data.
- MKTG-4 Comment/DM triage with drafted replies.
- MKTG-5 Brand voice guide; price/offer accuracy check against live snapshots; approval workflow.

## SEO — Website & search
- SEO-1 Public website/landing pages with inquiry form and WhatsApp click-to-chat.
- SEO-2 Destination guides, FAQs, visa pages generated with sources and human fact-check; quality and uniqueness checks to avoid thin/scaled spam.
- SEO-3 Sitemap, schema.org markup, Search Console data feeding topic ideas, content refresh jobs.

## ADS — Paid advertising
- ADS-1 Google Ads and Meta Ads adapters: campaign drafts, creatives, copy, budgets, A/B tests.
- ADS-2 Hard spend caps in code (daily, monthly, per campaign); pacing; offline conversion upload from real bookings.
- ADS-3 Anomaly detection with autonomous PAUSE (safety action); every other change needs approval.

## AN — Analytics
- AN-1 Dashboards: revenue, profit, margin, conversion by stage, lead sources, best destinations, campaign ROI, CAC, repeat rate, agent performance, outstanding payments, response times.
- AN-2 Weekly owner digest and anomaly alerts (WhatsApp/email).
- AN-3 Recommendations are proposals requiring approval; never auto-applied.

## ORC — Orchestration
- ORC-1 Manager component: priority queue, SLA timers, stuck-workflow detection, retries, dead-letter handling, human task routing, daily digest, urgent paging.
- ORC-2 Autonomy graduation tracker: per action type metrics -> promote/demote suggestions for the owner.

## SEC / OPS
- SEC-* see `07_SECURITY_COMPLIANCE.md`.
- OPS-1 Observability (logs, metrics, traces, alerts), LLM cost dashboard, backups with restore drills, runbooks, staging and production environments, rollback.
