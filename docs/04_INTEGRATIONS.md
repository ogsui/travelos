# 04 Integrations

Rule: official API first; mock always present; fragile options OFF by default. Never invent API details — implement from the provider docs the owner supplies and leave `TODO(owner)` otherwise.

| Area | Interface | Test impl | Real impl | Notes |
|---|---|---|---|---|
| Flights/hotels/transfers/activities | SupplierAdapter | SupplierSimulator | **TBO**, **Riya** official APIs | Sandbox first. Costs/contract: owner. |
| Supplier portal automation | PortalSessionAdapter | Playwright vs fixtures | Optional, OFF | Risks the agency's supplier accounts; no OTP/CAPTCHA bypass; low rate; owner must record risk acceptance. |
| Competitor prices | CompetitorPriceAdapter | Fixtures | ManualEntry; LicensedData (stub); PublicPageScraper | See scraping policy below. |
| WhatsApp | ChannelAdapter | Mock (outbox folder) | WhatsApp Business Cloud API | Needs Meta business verification, approved templates, opt-in. |
| Email | ChannelAdapter | Mailpit | SMTP/SES + IMAP/Gmail API | SPF/DKIM/DMARC; unsubscribe header on marketing mail. |
| SMS | ChannelAdapter | Mock | Provider with DLT registration | Indian rules: DLT + DND scrub. Low priority. |
| Instagram/Facebook | ChannelAdapter + PublishAdapter | Mock | Meta Graph API | DMs, comments, publishing, lead ads. App review needed. |
| LinkedIn | PublishAdapter | Mock | LinkedIn Pages API | Company page posting only. |
| X | PublishAdapter | Mock | X API | Paid tiers; low priority. |
| Threads | PublishAdapter | Mock | Threads API | |
| YouTube Shorts | PublishAdapter | Mock | YouTube Data API | Video rendered by templates (ffmpeg/MoviePy). |
| Google Business Profile | ChannelAdapter | Mock | Business Profile APIs | Messages, review links. |
| Google Ads | AdsAdapter | Mock | Google Ads API | Developer token approval. |
| Meta Ads | AdsAdapter | Mock | Meta Marketing API | |
| Analytics/SEO data | AnalyticsAdapter | Fixtures | GA4, Search Console APIs | |
| Payments | PaymentAdapter | MockGateway | Razorpay or Cashfree or PayU | Hosted payment links only; webhooks signed. |
| Accounting | ExportAdapter | Local files | openpyxl (Excel), Tally XML/CSV, Zoho Books (optional) | Accountant defines formats. |
| Visa info | VisaInfoAdapter | Static fixtures | Configured authoritative source (embassy pages/licensed API) | Human-verified; never LLM memory. |
| Weather | WeatherAdapter | Fixtures | Open-Meteo | |
| FX | FxAdapter | Fixed | Reputable FX API | Stored with timestamp. |
| Images | ImageAdapter | Placeholder | Image-generation API; owner photo library | Real properties: licensed/own photos only. |
| LLM | LLMProvider | Fake | OpenAI-compatible (incl. OpenCode Zen, OpenRouter, local), Anthropic | Policy flags per provider; see the section below. |
| PDF | — | WeasyPrint | WeasyPrint | |
| Storage | ObjectStore | MinIO | S3-compatible | Encrypted buckets. |

## LLM providers (task 006)
- `LLMProvider` is a small interface: `name`, `model`, `policy`, `pricing`, and one `complete(request)` call. The request type deliberately has no tools, functions or streaming field, so injected text has no structural path to an action.
- `FakeProvider` is deterministic and network-free; it is the default chain (`LLM_PROVIDER_CHAIN=["fake"]`) for tests, evals and CI. Live mode refuses it at settings validation.
- `OpenAICompatibleProvider` implements the documented `POST /chat/completions` shape and covers OpenAI, OpenCode Zen, OpenRouter and local compatible servers. `AnthropicProvider` implements the documented `POST /v1/messages` shape. Both take `base_url`, `model` and an optional key from settings; no endpoint, model name or price is guessed.
- Each provider has `no_training` and `synthetic_only` flags. Synthetic data may use any provider; `customer` or `public` data requires `no_training=True` and `synthetic_only=False`. A call with no eligible provider is blocked before any network request, recorded as `policy_blocked` and escalated to the owner. A caller may raise a prompt's declared data class (for example, mark a normally synthetic prompt as customer data) but may not lower it; a downgrade attempt is also blocked and escalated.
- The gateway owns retries (bounded attempts, exponential backoff with a cap) and a per-provider circuit breaker. Provider adapters map HTTP 429/401/403/5xx/4xx and timeouts to typed errors; they do not retry internally. Schema failures do not open the circuit because the provider was reachable.
- Provider payloads are sent with `temperature` and `max_tokens` from the prompt spec. There is no streaming and no tool/function calling in this layer.
- Prompt and response bodies are logged redacted in `llm_calls`; raw keys and Authorization headers are never logged. Prompts are rendered from `prompts/*.md` (see `prompts/README.md`).
- Pricing is integer paise per million input/output tokens (`LLM_*_INPUT_PAISE_PER_MILLION`, `LLM_*_OUTPUT_PAISE_PER_MILLION`). Defaults are `0`, meaning unpriced rather than free; the owner must supply a price list before live cost reporting is meaningful. Cost is rounded up to the next paise and never uses a float.
- `ProviderPricing.free` is an explicit declaration and must have zero rates. A daily budget of `0` means "free providers only": the gateway filters the chain to providers with `free=True` and blocks with `budget_blocked` if none remain. `0` never means "no cap". Only model IDs ending in `-free` are marked free automatically, so a paid or unpriced model is blocked under a zero budget.
- The daily budget uses the configured accounting timezone (`LLM_ACCOUNTING_TIMEZONE`, default `Asia/Kolkata`), not UTC. Spend resets at local midnight; a call at 00:30 IST belongs to that Indian day.
- `llm_calls` rows older than `LLM_CALL_RETENTION_DAYS` (default 90) are deleted by the `travelos.llm.purge_expired` beat task. The purge is the only path allowed past the append-only trigger, runs in one transaction, and writes an `llm.calls_purged` audit row with counts and the cutoff.

### Configured provider (owner decision, 2026-10-24)
- `LLM_PROVIDER=opencode` expands to the primary `LLM_MODEL=deepseek-v4.1-flash-free` and then `LLM_FALLBACK_MODEL=glm-5.3-flash-free`, both through the OpenAI-compatible adapter at `https://opencode.ai/inference/openai/v1/chat/completions`. Setting `LLM_FALLBACK_MODEL=` (empty) disables the fallback.
- The owner's daily budget is `0`, so both models must stay free. `LLM_OPENCODE_REVIEW_MODEL=beam-free` is reserved for the later reasoning/review step and is not in the chain yet.
- Redaction, prompt-injection checks and data-class downgrade prevention stay enabled for every provider. A free-tier provider is not automatically trusted: `LLM_OPENCODE_NO_TRAINING=true` and `LLM_OPENCODE_SYNTHETIC_ONLY=false` are the recorded policy flags, and customer data is still redacted. Beam Free is no-training but retains data for 30 days, so it must not receive customer data without a fresh owner decision.

### TODO(owner) — LLM account manager
1. OpenCode's documentation says free-tier chat models require the OpenCode client; confirm that a normal server-side `POST /chat/completions` with an API key works without it. Until a synthetic test call succeeds, the OpenCode path is configured but unverified.
2. Paid OpenCode price list (input/output, cached input, batch discount) in INR with an effective date; the repository treats non-`-free` models as unpriced.
3. Account rate limits (requests and tokens per minute), retry guidance, context window and max-output limits.
4. Is a no-training / data-processing addendum available for `customer` data, and what is the data-residency region?
5. May `beam-free` ever see customer data, given its 30-day retention, or should it stay synthetic-only?

## Scraping policy (MKT-1)
- Public pages only, no login, per-site flag default OFF; enabling requires the owner to record a decision in `docs/DECISIONS.md` (site, date, rationale, robots.txt and ToS reviewed).
- Throttle (e.g. <= 1 request / 10 s / site), cache results, identify honestly, keep volume proportional to real inquiries.
- No CAPTCHA solving, proxy rotation, fingerprint spoofing or other evasion. Blocked = `unavailable`.
- Parsers are fixture-tested because layouts change; expect breakage and keep ManualEntry as the permanent fallback.
- Competitor prices are retail/consumer prices with different bundling; only comparable, fresh snapshots affect the ceiling.

## Questions for TBO / Riya account managers (put answers in `docs/supplier_notes/`)
1. Sandbox/test credentials and data? Cost during development vs production?
2. Which products does our agency's contract include (domestic/international flights, hotels, transfers, activities, visas)?
3. Rate limits, caching rules (how long may prices be stored/shown), mandatory display of fare rules?
4. Hold / ticketing time limits, instant-purchase vs hold flows, how price changes are signalled?
5. Cancellation/void/refund APIs and charges; amendment support?
6. Wallet/credit mechanics: how is booking paid (deposit balance, credit limit)? Balance-read endpoint?
7. Webhooks or polling for schedule changes, cancellations, ticket status?
8. Rules on automated/AI-assisted booking and on storing passenger data?
9. Static content (hotel images/descriptions) licensing?
10. Support SLAs and escalation contacts for failed bookings.

## Provider onboarding checklist (owner, non-code)
Meta Business verification + WhatsApp Business number and templates · Meta developer app + Instagram/Facebook page permissions · Google Business Profile ownership · payment gateway account · domain + email DNS · Google Ads/Meta Ads accounts with billing caps · accountant meeting for tax tables · LLM provider account with a no-training agreement for real data.

## Public web form (task 009b)
- `POST /channels/web-form` normalizes visitor messages through the channel service. It accepts no CRM identifiers and never links an unverified email/phone claim to a customer or contact.
- Accepted message bodies are encrypted at rest. Name, email and phone form fields are validated in memory but not stored; submissions do not grant consent or trigger outbound acknowledgements.
- A honeypot and 5-per-10-minute source limiter guard intake. Test mode uses an in-memory limiter; shadow/live use the atomic Redis limiter. The direct connection address is keyed before it is used as the limiter key; forwarded headers are ignored.
