# 08 Testing, Evaluation & Rollout

## Test pyramid
`tests/unit` (pure logic, property tests) · `tests/integration` (DB, Redis, adapters vs mocks) · `tests/contract` (every adapter interface; runs against mock and, when available, sandbox) · `tests/eval` (LLM quality + adversarial) · `tests/e2e` (inquiry -> invoice on synthetic data, Playwright for the dashboard).

## Synthetic dataset (task 007) — everything obviously fake
| Set | Size / content |
|---|---|
| Customers | ~500 with households, bookings history, mixed consent states (granted/withdrawn/unknown), segments |
| Inquiries + gold requirements | 150+: domestic flights; international flights; family packages (4-6 pax with children/infants); honeymoon; pilgrimage; corporate multi-city; groups/MICE; visa-only; hotel-only; vague; contradictory; Hinglish/Hindi; mid-conversation date changes; misspelt names; long layovers |
| Gold quotes | 40 hand-built (components, prices, margins) incl. owner-supplied samples when available |
| Supplier simulator | Time-varying prices, sold-out, price jumps between quote and booking, timeouts, partial failures, hold expiry |
| Competitor snapshots | Comparable, non-comparable, stale, blocked, missing |
| Channel fixtures | WhatsApp/email/IG/FB/GBP webhook payloads including duplicates, out-of-order, media, STOP messages |
| Payments | Full, partial, failed, duplicate webhook, late, refund requests |
| Marketing | Past posts with engagement, ad metrics, SEO keyword data |
| Adversarial | Prompt injection in messages/emails/scraped pages, abusive customers, PII fishing, "refund me now", fake urgency, wrong language, giant messages |

## Eval harness (task 018)
`make eval` runs: parser field accuracy; missing-info behaviour (asks vs guesses); package match vs gold; pricing vs golden cases; reply quality rubric (helpful, accurate, on-brand, no unsupported promises; LLM-judge + human sample); content accuracy (no unverified prices/claims); injection containment; latency/cost per task. Results stored per prompt version; CI fails on regression beyond tolerance.

## Suggested gates (tune with real data)
| Gate | Criterion |
|---|---|
| Parser | >= 95% required-field accuracy; 0 silent assumptions |
| Packages | >= 85% component match vs gold |
| Pricing | 0 failures in unit/property/golden tests; staff price within tolerance on >= 80% of shadow cases; never below floor |
| Replies | >= 95% "approved unchanged or minor edit" over 100 drafts before A2 |
| Injection suite | 100% contained |
| Speed | p95 draft quote < 10 min |
| Reliability | Supplier/scraper failures never block a quote in failure-injection tests |

## Failure-injection tests
Supplier timeout/5xx/slow; price changes mid-flow; duplicate webhooks; Redis restart; worker crash mid-booking (idempotency); WhatsApp 24h window expiry; template rejection; payment webhook before link record; clock skew; huge attachments.

## Shadow mode (task 039)
30 days or >= 100 real inquiries (whichever is longer): real inquiries are copied in by staff or via read-only channel; the system produces parsed requirements, quotes and drafts; **nothing is sent**. Staff record what they actually did. Weekly review report: accuracy, price difference, time saved, near-misses, what staff changed and why. Owner signs a go/no-go.

## Pilot (task 040)
Live mode with all actions at A1; real customers, limited scope (e.g. domestic flights + hotels, a few staff, one channel). Daily review for 2 weeks. Expand scope only after gates hold. Rollback = flip kill switches + revert to manual process (documented, rehearsed).

## Go-live checklist
Accountant signed tax tables and invoice templates · Lawyer reviewed consent/DPDP/marketing setup · Backups restore-tested · Kill switches rehearsed · On-call plan · No-training LLM agreement in place · All LOCKED actions verified · Staff trained · Customer-facing disclosures (AI assistance, privacy notice) published.
