# 06 Pricing Engine

Deterministic, explainable, fully unit- and property-tested. **No LLM calls anywhere in this module.** All amounts are integer paise.

## Inputs
- Itinerary items with immutable supplier `price_snapshots` (net, supplier taxes/fees, currency, fare rules, expiry).
- `pricing_rule_sets` (owner-approved, versioned) and `tax_rule_tables` (accountant-approved, versioned).
- Customer segment (new, repeat, corporate, referral, group), lead time, season, inventory hints.
- Optional comparable competitor snapshots (fresh + comparability >= threshold).
- FX rate snapshot (if supplier currency != INR) + configured forex buffer.

## Calculation
```
supplier_total      = sum(net + supplier_taxes_fees)            per item, converted at fx snapshot (+ buffer)
markup              = rule(product, supplier, destination, segment, season, lead_time)
                      -> fixed_paise + percent (tiers allowed), capped by max_markup
service_fee         = rule(...) (optional)
gateway_fee         = passthrough or absorbed per policy
pre_tax_price       = supplier_total + markup + service_fee
taxes               = tax_table(product_type, ...)  applied per accountant rules (GST/TCS etc. live in the table)
customer_price      = round_rule(pre_tax_price + taxes + gateway_fee)

floor_price         = supplier_total + min_margin(rule) + direct_costs(gateway, fixed handling) + taxes_on_floor
target_price        = supplier_total + target_margin(rule) + taxes ...
ceiling_price       = min( max_markup_cap_price,
                           trusted_competitor_price - undercut_amount )   # competitor term only if a trusted snapshot exists
recommended_price   = clamp(target_price, floor_price, ceiling_price)
```
- If `floor_price > ceiling_price`: status `UNCOMPETITIVE` — no automatic quote; owner decides (discount, drop the item, or quote anyway).
- If the final price < floor: status `BELOW_FLOOR` — owner-only approval (LOCKED).
- Discounts come from a per-segment discount budget and can never push below floor without owner approval.
- Rounding rules configurable (e.g. nearest Rs 10/50/100), applied last, never below floor.

## Outputs
`PriceResult { floor, target, ceiling, recommended, customer_price, status, lines[], explanation[], rule_version, tax_table_version, snapshots[], fx_snapshot, warnings[] }`
Every result is stored immutably in `price_calculations` and referenced by the quote revision.

## Competitor logic
- Use only snapshots fetched within `max_age`, with comparability score >= threshold (same route/dates/pax/fare class/baggage/refundability).
- Ignore outliers (e.g. > 3 MAD from the median of comparable snapshots).
- Missing/blocked data never blocks pricing; the ceiling falls back to `max_markup_cap_price` and a warning is attached.

## Safety checks
- Quote carries `expires_at = min(snapshot.expires_at, configured max validity)`.
- Revalidation tolerance (absolute and %) before booking; breach -> human decision.
- Currency mixing forbidden within one total; conversions only via stored FX snapshots.

## Rule loading and `NEEDS_CONFIG` (task 015a)
`config/business_rules.yaml` is the owner's file; `pricing/rules.py` reads only its
`version`, `pricing` and `tax` sections and ignores sections owned by other modules.
Every money field may be `null` while the owner is still deciding, and a `null` is never
defaulted:

- `load_pricing_rules(path)` returns a `RulesetLoadResult` with status `OK` only when
  every required value is present; otherwise the status is `NEEDS_CONFIG`, `ruleset` is
  `None` and `gaps` lists each missing path (for example
  `pricing.margins.hotel.min_paise`). A missing file is one gap with reason
  `missing_file`, not an exception.
- A malformed value (wrong type, negative amount, more than two percentage decimals,
  `min > target > max`, unknown product key) raises `PricingConfigError` naming the
  field. A typo must not look like an unfinished placeholder.
- Percentages are converted once, at load time, to integer basis points (100 bps = 1%),
  so no float reaches the calculator. `Money` remains integer paise.
- `pricing.margin_combination` is required owner data: `max` treats `min_paise` as a
  floor on the percentage margin, `sum` adds the two. The engine never picks one.
- The file carries a `version` and the loader returns a sha256 `source_hash` of the exact
  bytes read, so a stored calculation can name the rule file it used. Database-backed
  versioning/approval (`pricing_rule_sets`) is task 015c.
- Tax **rates** are not in this file. `tax.tax_table_version`, `gst_basis_by_product` and
  the TCS reference point at the accountant-approved table loaded by 015c; a placeholder
  there also yields `NEEDS_CONFIG`.

The shipped file is intentionally all-placeholder, so the system cannot quote a customer
price until the owner fills it in. This section describes rule loading only; the price
assembly is 015b2.

## Exact primitives (task 015b1)
`pricing/arithmetic.py` and `pricing/tax.py` contain the arithmetic the assembly step
(015b2) uses. There are no floats, no LLM calls and no policy defaults:

- **FX.** An `FxSnapshot` stores an exact rational rate (`numerator / denominator`, quote
  units per base unit), the observation time and source. `convert` multiplies by the rate
  and the configured forex buffer, rounds half-up once, and refuses a mismatched
  currency. A missing or stale snapshot is the caller's refusal, not an identity rate.
- **Rounding.** `round_money` implements nearest/up/down to a configured step;
  `round_not_below` raises the rounded price to the next step at or above the floor, so
  rounding can never make a price cross the floor.
- **Margins.** A percentage always applies to the supplier total. `margin_band` returns
  minimum/target/cap using the owner's `margin_combination`: `max` treats `min_paise` as
  a floor on the percentage, `sum` adds it. If an absolute floor is above the percentage
  cap the result is `FLOOR_ABOVE_CAP`, not a silently clamped price.
- **Taxes.** A `TaxTable` is a versioned, accountant-approved set of rows keyed by
  product, GST basis and optional value slab. `resolve_tax` produces a `TaxLine` only
  when exactly one approved row matches; otherwise the status is `UNAPPROVED`,
  `NOT_CONFIGURED` or `AMBIGUOUS` and no zero is assumed. A `not_applicable` product
  still needs an explicit zero-rated row. TCS rows are not modelled yet; enabling TCS
  must refuse to quote until the accountant's TCS table exists.

Synthetic values in tests are engineering fixtures, not Indian tax advice or owner
margins. The real table must be supplied and approved by the accountant before 015b2 can
produce a customer price.

## Test requirements
Property tests (hypothesis): determinism; recommended >= floor unless status flagged; monotonic in supplier cost; line items sum exactly to totals; rounding never crosses the floor; tax never negative and always traceable to a table row; no float types in the module (static test); FX conversion round-trips within tolerance rules.
Golden tests: >= 30 hand-checked scenarios supplied by the owner/accountant (family package, corporate multi-city, group, international flight with TCS case, hotel-only, visa-only, refundable vs non-refundable).

## Open decisions (owner/accountant must answer before task 015 is "done"; see `11_BUSINESS_RULES_INTERVIEW.md`)
Margin structure per product; minimum margin; whether percentages apply to supplier cost (current reading) or selling price; the fixed/percentage combination (`margin_combination`); discount budgets; rounding; GST treatment per product (commission vs principal basis); TCS applicability, thresholds and base; the accountant-approved tax table; gateway fee rate and policy; forex buffer and rate source; maximum quote validity; revalidation tolerance; 30 hand-checked golden scenarios.
