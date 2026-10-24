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

### TODO(owner) — LLM account manager
1. Which provider(s) and exact model IDs should be primary and fallback?
2. What is the per-million-token price (input/output, and any cached-input or batch discount) in INR, with an effective date?
3. Is a no-training / data-processing addendum available for `customer` data, and what is the data-residency region?
4. What are the account rate limits (requests and tokens per minute) and the retry guidance?
5. Which model context window and max-output limit apply, so prompt `max_tokens` values stay valid?

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
