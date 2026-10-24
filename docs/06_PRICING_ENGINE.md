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

## Test requirements
Property tests (hypothesis): determinism; recommended >= floor unless status flagged; monotonic in supplier cost; line items sum exactly to totals; rounding never crosses the floor; tax never negative and always traceable to a table row; no float types in the module (static test); FX conversion round-trips within tolerance rules.
Golden tests: >= 30 hand-checked scenarios supplied by the owner/accountant (family package, corporate multi-city, group, international flight with TCS case, hotel-only, visa-only, refundable vs non-refundable).

## Open decisions (owner/accountant must answer before task 015 is "done"; see `11_BUSINESS_RULES_INTERVIEW.md`)
Margin structure per product; minimum margin; discount budgets; rounding; GST treatment per product (commission vs principal basis); TCS applicability and thresholds; gateway fee policy; forex buffer; maximum quote validity.
