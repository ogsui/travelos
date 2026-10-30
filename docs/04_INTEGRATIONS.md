# 04 Integrations

Rule: for supplier and competitor price/inventory collection, use authorized portal pages rather than APIs, as directed by the owner. For other integrations, official API first; mock always present; fragile options OFF by default. Never invent API details — implement from the provider docs the owner supplies and leave `TODO(owner)` otherwise.

| Area | Interface | Test impl | Real impl | Notes |
|---|---|---|---|---|
| Flights/hotels/transfers/activities | SupplierAdapter | SupplierSimulator | Authorized supplier portal for price/inventory collection; no API for this collection path | Portal account and automated access must be authorized. Booking actions are separate and approval-gated. |
| Supplier portal automation | PortalSessionAdapter | Playwright vs fixtures | Logged-in portal automation, OFF by default | Owner enables each portal; authorized account only; pause for operator handling of permitted CAPTCHA/OTP/MFA challenges; access blocks are unavailable. |
| Competitor prices | CompetitorPriceAdapter | ManualEntry (in-memory), LicensedData stub | Authorized logged-in competitor pages; licensed feed (pending owner docs) | No API for competitor-price collection. Manual entry is built (014a2a); the licensed feed is a stub until the owner supplies provider docs. The collection loop (014a2b2) contacts a site only when both the operator `scraper_*` switch and the owner's database flag are on, reuses a fresh snapshot, and throttles failures. Portal terms review and the operator resume flow are required before 014b. |
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


## Supplier adapters (task 012a1)
- `SupplierAdapter` is the port: `search`, `price`, `rules`, `hold`, `book`, `cancel`, `retrieve`, `wallet_balance`. It returns normalized `Offer`/`FareRules`/`CancellationRules`/`Hold`/`Booking` models with integer paise and UTC timestamps.
- `SupplierSimulator` is the fixture adapter. It reads `data/synthetic/supplier_inventory.jsonl` and `supplier_scenarios.jsonl` and reproduces price drift, sold out, timeouts, partial failures and hold expiry. Failures are typed and immediate; it never sleeps for the fixture's `after_ms` and never calls the network. Holds and bookings live in process memory and are not thread-safe.
- `tests/contract/test_supplier_contract.py` is the shared suite. The simulator passes it today; an authorized sandbox adapter is added to its `TARGET_FACTORIES` only when the owner supplies access, and CI must build that target without network access.
- `book`/`cancel` are on the interface because the booking orchestrator (task 020) will need them. No API route or service in this slice can reach them, and the adapter itself has no approval logic: the orchestrator and approval engine own that gate. The adapter never moves money; `wallet_balance` is a read.
- Fare rules are supplier statements, not customer policy. The simulator's cancellation refund split is synthetic fixture behaviour, not an owner refund or cancellation rule.
- A search may contain several legs for one supplier. A leg that fails is reported as a `SearchFailure`, so the caller receives partial results with a flag instead of an empty answer.
- Portal price collection for tasks 12b-12d remains OFF and blocked on the owner's authorized accounts, permitted page workflows and terms/access confirmation. No portal endpoint, page workflow or parser is invented in this slice.


## Supplier search orchestration (task 012a2)
- `SupplierSearchService` calls each adapter in parallel with its own deadline. A timeout, an open circuit or any typed adapter failure becomes a `SearchFailure` per requested leg; offers from the other suppliers are still returned with `complete=False`. Unexpected adapter exceptions are contained and logged by type only, never with the raw provider text.
- Successful adapter results are recorded through `SnapshotStore` as `price_snapshots`. An unexpired batch with the same request hash is reused on the next search (`cached=True`); `use_cache=False` forces a fresh supplier read. The hash covers the normalized query only, never customer data.
- Circuit breakers are in-process and per supplier. `SUPPLIER_CIRCUIT_FAILURE_THRESHOLD` and `SUPPLIER_CIRCUIT_RESET_SECONDS` control opening and the half-open probe. Opening affects this process only; a restart clears it. Cached results bypass an open circuit.
- The orchestration timeout is a backstop, not cancellation: Python worker threads cannot be killed. Each portal adapter must enforce its own page/socket deadline, and a timed-out or access-denied portal must be treated as unavailable until an operator resolves access.
- There is no API or worker wired to the service yet. `book`/`cancel` remain unreachable by any route and stay behind the future booking orchestrator (task 020) and approval engine.


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

## Portal price-collection policy (MKT-1)
- Supplier and competitor price/inventory collection may use logged-in pages only with an agency-authorized account and where the provider permits automated access. Per-portal flag defaults OFF; enabling requires the owner to record the site, account owner, date, rationale, and terms/access review in `docs/DECISIONS.md`.
- Keep credentials in the approved secret store; never put credentials, cookies, or session tokens in source control, logs, fixtures, or prompts. Throttle (e.g. <= 1 request / 10 s / portal), cache results, identify automation honestly where required, and keep volume proportional to real inquiries.
- CAPTCHA or OTP/MFA pauses the run in `needs_human` and notifies an authorized operator. Where provider terms permit, the operator completes the challenge interactively in the authorized session; an explicit operator resume is required, and automation verifies access is restored before continuing. Access-denied and anti-bot blocks are recorded as `blocked` and must not be retried until an authorized operator resolves the access issue and records a resume; the 014a2b2 collection loop enforces this. No programmatic challenge solving, proxy rotation, fingerprint spoofing, or other access-control evasion.
- Parsers are fixture-tested because layouts change; keep ManualEntry or a licensed source as fallback. Portal price access does not authorize booking, ticketing, cancellation, or refund actions.
- Competitor prices are retail/consumer prices with different bundling; only comparable, fresh snapshots affect the ceiling.
- The 014a2b2 collection loop never retries a `blocked` run automatically: an anti-bot/CAPTCHA block needs an authorized operator to resolve access and record a resume (014b). An `unavailable` run is retried only after `min_interval` (10 minutes by default), and a fresh snapshot (24 hours by default) is reused instead of contacting the site again. Manual entry is not cacheable, so a new human entry is never hidden; an entry already stored is not appended twice.

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

## Email channel adapter (task 009c)
- `EmailChannelAdapter` implements the `ChannelAdapter` protocol for inbound RFC 2822 parsing and outbound delivery.
- Inbound: `parse_raw_email` decodes MIME/multipart headers, extracts `Message-ID` for deduplication, threads replies via `In-Reply-To`/`References`, parses dates to timezone-aware UTC, and stores bodies encrypted at rest through `ChannelService`.
- Outbound: enforces mode gates (blocked in `test` and `shadow` modes), the `email` kill switch, recipient consent/suppression (`ConsentService.can_send`), and approval policies (checking `ApprovalRequest.status == 'approved'` or requiring approval for A1 actions like `message.sales_reply`).
- Transports: `MailpitTransport` provides local development SMTP relay on port 1025 without external network access; `SMTPTransport` provides standard authenticated TLS/STARTTLS delivery for production; `NullTransport` silently discards for tests.

## WhatsApp channel adapter (task 009d)
- `WhatsAppChannelAdapter` implements the same channel contract as email: inbound events land in `conversations`/`messages` (bodies encrypted, Meta `wamid` as the dedupe key) and outbound sends pass the mode, kill-switch, consent and approval gates.
- The subscription handshake is `GET /channels/whatsapp/webhook` (`hub.mode=subscribe`, constant-time token comparison, challenge echoed as plain text; `403` otherwise). The inbound endpoint is `POST /channels/whatsapp/webhook` and checks Meta's `X-Hub-Signature-256` HMAC over the raw body before parsing.
- Credentials are owner-supplied (`WHATSAPP_VERIFY_TOKEN`, `WHATSAPP_APP_SECRET`, `WHATSAPP_PHONE_NUMBER_ID`, `WHATSAPP_ACCESS_TOKEN`, `WHATSAPP_API_BASE_URL`). If the verify token or app secret is unset, `test` mode still accepts fixtures, while `shadow` and `live` answer `503 whatsapp_webhook_not_configured` — an unverified webhook is never trusted outside test mode.
- Parsing covers text, image, document, video, audio/voice, interactive button/list replies, quick-reply buttons and location. Media is stored as reference strings (`whatsapp:<type>:<media-id>[:<filename>]`), never downloaded or inlined into prompts. Delivery-status batches produce no messages. Malformed JSON gives `422`; unknown or partial entries are skipped so one bad item does not lose the rest of a retried batch.
- Outbound gates, in order: `test`/`shadow` block all sends, `KILL_SWITCHES__WHATSAPP` must be ON, consent/suppression is checked for the contact and purpose, A0/A1 actions need an approved `ApprovalRequest`, and messages outside the 24-hour customer-care window must use an approved template. An unapproved template is rejected even inside the window.
- Transports: `MockWhatsAppTransport` records sends for tests; `CloudAPIWhatsAppTransport` posts to the Graph API `/{phone_number_id}/messages` endpoint, strips the recipient to digits and uses an owner-confirmed template language. Tests never call it.
- Delivery status is `unavailable` until Meta status webhooks are persisted; the adapter has no send path wired into the orchestrator yet (tasks 016/023).
- Contract tests use recorded Cloud API shapes in `tests/fixtures/whatsapp/webhook_events.json`.

### TODO(owner) — Meta / WhatsApp account manager
1. WABA ID, `phone_number_id` and a system-user access token for the agency number, plus whether the test number or the production number is in scope for the pilot.
2. Exact approved template names, categories and language codes for the six intended messages (acknowledgement, quote ready, payment reminder, booking confirmed, travel documents, disruption alert). `APPROVED_TEMPLATES` in the adapter is a placeholder until this list arrives.
3. Has Meta business verification completed, and what is the messaging tier/limit for our number?
4. Where should inbound media be stored (bucket, retention, who can read it), and may attachments be downloaded automatically or only on operator action?
5. Which authorized operator completes Meta OTP/MFA or review challenges, and what is the escalation path if the number is restricted?

