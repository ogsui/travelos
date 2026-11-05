"""Deterministic quote assembly tests (task 015b2).

Every margin, tax rate and rounding value here is a synthetic engineering
fixture, not owner or accountant policy. The tests pin the contract: a complete
synthetic rule set yields an explainable OK quote, missing owner/accountant data
yields ``NEEDS_CONFIG``, and a trusted competitor ceiling or an explicit owner
proposal changes the status rather than being silently ignored.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from travelos.core.money import Money, money
from travelos.pricing.arithmetic import FxSnapshot
from travelos.pricing.calculator import (
    ItemCost,
    PriceRequest,
    PriceStatus,
    price_quote,
)
from travelos.pricing.models import (
    CompetitorPolicy,
    CustomerSegment,
    GatewayFeePolicy,
    GstBasis,
    MarginCombination,
    MarginRule,
    PricingProduct,
    PricingRuleSet,
    RevalidationTolerance,
    RoundingMode,
    RoundingRule,
    TcsPolicy,
)
from travelos.pricing.tax import TaxRateRow, TaxTable

NOW = datetime(2026, 10, 31, 9, 0, tzinfo=UTC)


def ruleset(
    *,
    gateway_fee_policy: GatewayFeePolicy = GatewayFeePolicy.PASSTHROUGH,
    rounding_step_paise: int = 100,
    basis: GstBasis = GstBasis.COMMISSION,
) -> PricingRuleSet:
    margins = {
        product: MarginRule(min_paise=5_000, min_bps=300, target_bps=800, max_bps=1_500)
        for product in PricingProduct
    }
    return PricingRuleSet(
        version=3,
        rounding=RoundingRule(mode=RoundingMode.NEAREST, step_paise=rounding_step_paise),
        quote_validity_hours=168,
        revalidation=RevalidationTolerance(absolute_paise=50_000, percent_bps=200),
        forex_buffer_bps=150,
        gateway_fee_policy=gateway_fee_policy,
        margin_combination=MarginCombination.MAX,
        margins=margins,
        segment_discount_budget_bps={segment: 200 for segment in CustomerSegment},
        competitor=CompetitorPolicy(
            max_age=timedelta(hours=24),
            min_comparability=80,
            undercut_paise=10_000,
        ),
        tax_table_version=1,
        gst_basis_by_product={product: basis for product in PricingProduct},
        tcs=TcsPolicy(enabled=False),
    )


def tax_table(
    *,
    basis: GstBasis = GstBasis.COMMISSION,
    rate_bps: int = 500,
    products: tuple[PricingProduct, ...] = tuple(PricingProduct),
    approved: bool = True,
) -> TaxTable:
    return TaxTable(
        version=1,
        approved_by_accountant=approved,
        approved_on=date(2026, 10, 31),
        rows=tuple(
            TaxRateRow(
                product=product,
                basis=basis,
                rate_bps=0 if basis is GstBasis.NOT_APPLICABLE else rate_bps,
                reference=f"synthetic-{product.value}-{basis.value}",
            )
            for product in products
        ),
    )


def item(
    *,
    reference: str = "hotel-1",
    product: PricingProduct = PricingProduct.HOTEL,
    net_paise: int = 100_000,
    supplier_taxes_paise: int = 0,
    currency: str = "INR",
    expires_at: datetime | None = None,
) -> ItemCost:
    return ItemCost(
        reference=reference,
        product=product,
        net=money(net_paise, currency),
        supplier_taxes=money(supplier_taxes_paise, currency),
        expires_at=expires_at,
    )


def request(
    *,
    rules: PricingRuleSet | None = None,
    table: TaxTable | None = None,
    items: tuple[ItemCost, ...] | None = None,
    gateway_fee: Money | None = None,
    competitor_reference: Money | None = None,
    proposed_price: Money | None = None,
    fx: tuple[FxSnapshot, ...] = (),
) -> PriceRequest:
    return PriceRequest(
        rules=rules if rules is not None else ruleset(),
        tax_table=table if table is not None else tax_table(),
        items=items if items is not None else (item(),),
        segment=CustomerSegment.NEW,
        gateway_fee=gateway_fee if gateway_fee is not None else money(0),
        competitor_reference=competitor_reference,
        proposed_price=proposed_price,
        fx_snapshots=fx,
        now=NOW,
    )


def usd_snapshot() -> FxSnapshot:
    return FxSnapshot(
        base_currency="USD",
        quote_currency="INR",
        numerator=8_325,
        denominator=100,
        fetched_at=NOW - timedelta(minutes=5),
        source="synthetic test rate",
    )


# ---------------------------------------------------------------------------
# A complete synthetic quote
# ---------------------------------------------------------------------------


def test_a_complete_synthetic_quote_is_explainable() -> None:
    result = price_quote(request())

    assert result.status is PriceStatus.OK
    assert result.currency == "INR"
    assert (result.floor.paise, result.target.paise, result.ceiling.paise) == (
        105_250,
        108_400,
        115_750,
    )
    assert result.recommended == money(108_400)
    assert result.customer_price == money(108_400)
    assert result.expires_at == NOW + timedelta(hours=168)
    assert result.rule_version == 3
    assert result.tax_table_version == 1
    assert sum(line.amount.paise for line in result.lines) == 108_400
    assert any("synthetic-hotel-commission" in line for line in result.explanation)
    assert any("8%" in line for line in result.explanation)


def test_passthrough_gateway_fee_is_added_on_top() -> None:
    result = price_quote(request(gateway_fee=money(2_000)))

    assert result.status is PriceStatus.OK
    assert (result.floor.paise, result.target.paise, result.ceiling.paise) == (
        105_250,
        108_400,
        115_750,
    )
    assert result.customer_price == money(110_400)
    assert sum(line.amount.paise for line in result.lines) == 110_400


def test_absorbed_gateway_fee_is_inside_floor_and_target() -> None:
    result = price_quote(
        request(
            rules=ruleset(gateway_fee_policy=GatewayFeePolicy.ABSORB),
            gateway_fee=money(2_000),
        )
    )

    assert result.status is PriceStatus.OK
    assert (result.floor.paise, result.target.paise, result.ceiling.paise) == (
        107_250,
        110_400,
        117_750,
    )
    assert result.customer_price == money(110_400)
    assert sum(line.amount.paise for line in result.lines) == 110_400


def test_principal_gst_taxes_the_full_price() -> None:
    result = price_quote(
        request(rules=ruleset(basis=GstBasis.PRINCIPAL), table=tax_table(basis=GstBasis.PRINCIPAL))
    )

    # floor 105000 + 5% of 105000 = 110250; target 108000 + 5400 = 113400.
    assert result.status is PriceStatus.OK
    assert result.floor == money(110_250)
    assert result.target == money(113_400)
    assert result.customer_price == money(113_400)


def test_a_not_applicable_product_needs_an_explicit_zero_row() -> None:
    result = price_quote(
        request(
            rules=ruleset(basis=GstBasis.NOT_APPLICABLE),
            table=tax_table(basis=GstBasis.NOT_APPLICABLE),
        )
    )

    assert result.status is PriceStatus.OK
    assert result.target == money(108_000)


# ---------------------------------------------------------------------------
# Competitor ceiling and proposals
# ---------------------------------------------------------------------------


def test_a_ceiling_below_the_floor_is_uncompetitive() -> None:
    result = price_quote(
        request(competitor_reference=money(110_000))  # 110000 - 10000 = 100000
    )

    assert result.status is PriceStatus.UNCOMPETITIVE
    assert result.recommended is None
    assert result.customer_price is None
    assert result.floor == money(105_250)
    assert result.ceiling == money(100_000)
    assert any("competitor" in warning for warning in result.warnings)


def test_a_ceiling_between_floor_and_target_sets_the_recommendation() -> None:
    result = price_quote(
        request(competitor_reference=money(111_000))  # 111000 - 10000 = 101000
    )

    assert result.status is PriceStatus.UNCOMPETITIVE

    result = price_quote(
        request(competitor_reference=money(116_000))  # 116000 - 10000 = 106000
    )

    assert result.status is PriceStatus.OK
    assert result.recommended == money(106_000)
    assert result.customer_price == money(106_000)


def test_without_a_trusted_competitor_the_cap_sets_the_ceiling() -> None:
    result = price_quote(request())

    assert result.ceiling == money(115_750)
    assert any("markup cap" in warning for warning in result.warnings)


def test_a_proposal_below_the_floor_is_below_floor() -> None:
    result = price_quote(request(proposed_price=money(100_000)))

    assert result.status is PriceStatus.BELOW_FLOOR
    assert result.recommended == money(100_000)
    assert result.customer_price == money(100_000)
    assert any("approval" in warning for warning in result.warnings)


def test_a_below_floor_proposal_with_a_gateway_fee_still_reconciles() -> None:
    result = price_quote(request(proposed_price=money(100_000), gateway_fee=money(2_000)))

    assert result.status is PriceStatus.BELOW_FLOOR
    assert result.customer_price == money(102_000)
    assert sum(line.amount.paise for line in result.lines) == 102_000
    assert result.lines[-1].kind == "proposal"


def test_a_proposal_above_the_ceiling_is_uncompetitive() -> None:
    result = price_quote(
        request(competitor_reference=money(116_000), proposed_price=money(110_000))
    )

    assert result.status is PriceStatus.UNCOMPETITIVE
    assert result.customer_price is None


def test_floor_and_ceiling_that_no_rounding_step_can_satisfy_is_uncompetitive() -> None:
    # floor 105250, ceiling 105260, target 108400: nearest 105300 overshoots and
    # 105200 is below the floor, so no valid rounded price exists.
    result = price_quote(request(competitor_reference=money(115_260)))

    assert result.status is PriceStatus.UNCOMPETITIVE
    assert result.customer_price is None
    assert result.floor is not None and result.floor.paise == 105_250
    assert result.ceiling is not None and result.ceiling.paise == 105_260
    assert any("rounding step" in warning for warning in result.warnings)


# ---------------------------------------------------------------------------
# Fail closed
# ---------------------------------------------------------------------------


def test_a_missing_tax_row_is_needs_config() -> None:
    result = price_quote(
        request(
            items=(item(product=PricingProduct.FLIGHT_DOMESTIC, reference="flight-1"),),
            table=tax_table(products=(PricingProduct.HOTEL,)),
        )
    )

    assert result.status is PriceStatus.NEEDS_CONFIG
    assert result.customer_price is None
    assert any("no approved tax row" in gap for gap in result.gaps)


def test_an_unapproved_tax_table_is_needs_config() -> None:
    result = price_quote(request(table=tax_table(approved=False)))

    assert result.status is PriceStatus.NEEDS_CONFIG
    assert any("approved by the accountant" in gap for gap in result.gaps)


def test_tcs_enabled_is_needs_config_until_the_table_exists() -> None:
    rules = ruleset()
    rules = rules.model_copy(update={"tcs": TcsPolicy(enabled=True, rules_ref="synthetic-tcs")})

    result = price_quote(request(rules=rules))

    assert result.status is PriceStatus.NEEDS_CONFIG
    assert any("TCS" in gap for gap in result.gaps)


# ---------------------------------------------------------------------------
# FX and expiry
# ---------------------------------------------------------------------------


def test_a_foreign_currency_item_is_converted_with_the_buffer() -> None:
    result = price_quote(
        request(
            items=(item(reference="hotel-usd", net_paise=10_000, currency="USD"),),
            fx=(usd_snapshot(),),
        )
    )

    assert result.status is PriceStatus.OK
    assert result.floor == money(871_606)
    assert result.target == money(915_967)
    assert result.customer_price == money(916_000)
    assert any("USD/INR 8325/100" in line for line in result.explanation)
    assert any("150 bps" in line for line in result.explanation)
    assert any("synthetic test rate" in line for line in result.explanation)


def test_a_missing_fx_snapshot_is_needs_config() -> None:
    result = price_quote(request(items=(item(net_paise=10_000, currency="USD"),)))

    assert result.status is PriceStatus.NEEDS_CONFIG
    assert any("USD" in gap for gap in result.gaps)


def test_an_expired_item_is_needs_config() -> None:
    result = price_quote(request(items=(item(expires_at=NOW - timedelta(seconds=1)),)))

    assert result.status is PriceStatus.NEEDS_CONFIG
    assert any("expired" in gap for gap in result.gaps)


def test_expiry_uses_the_soonest_item_or_the_configured_validity() -> None:
    later = price_quote(request(items=(item(expires_at=NOW + timedelta(hours=200)),)))
    sooner = price_quote(
        request(
            items=(
                item(reference="a", expires_at=NOW + timedelta(hours=20)),
                item(
                    reference="b",
                    product=PricingProduct.TRANSFER,
                    expires_at=NOW + timedelta(hours=2),
                ),
            )
        )
    )

    assert later.expires_at == NOW + timedelta(hours=168)
    assert sooner.expires_at == NOW + timedelta(hours=2)


# ---------------------------------------------------------------------------
# Input validation and properties
# ---------------------------------------------------------------------------


def test_items_must_have_unique_references_and_matching_currencies() -> None:
    with pytest.raises(ValidationError, match="reference"):
        request(items=(item(), item()))

    with pytest.raises(ValidationError, match="currency"):
        ItemCost(
            reference="mixed",
            product=PricingProduct.HOTEL,
            net=money(100, "USD"),
            supplier_taxes=money(100),
        )


def test_the_quote_is_deterministic() -> None:
    first = price_quote(request(competitor_reference=money(116_000)))
    second = price_quote(request(competitor_reference=money(116_000)))

    assert first == second


@given(
    st.integers(min_value=34_000, max_value=4_000_000),
    st.integers(min_value=0, max_value=1_000_000),
)
@settings(max_examples=40)
def test_a_higher_supplier_cost_never_lowers_the_price(net_paise: int, extra_paise: int) -> None:
    lower = price_quote(request(items=(item(net_paise=net_paise),)))
    higher = price_quote(request(items=(item(net_paise=net_paise + extra_paise),)))

    assert lower.status is PriceStatus.OK
    assert higher.status is PriceStatus.OK
    assert lower.customer_price is not None
    assert higher.customer_price is not None
    assert lower.customer_price.paise <= higher.customer_price.paise


@given(
    st.integers(min_value=0, max_value=10_000_000),
    st.one_of(st.none(), st.integers(min_value=0, max_value=20_000_000)),
)
@settings(max_examples=50)
def test_price_ordering_properties(net_paise: int, competitor: int | None) -> None:
    result = price_quote(
        request(
            items=(item(net_paise=net_paise),),
            competitor_reference=money(competitor) if competitor is not None else None,
        )
    )

    assert result.status in {
        PriceStatus.OK,
        PriceStatus.UNCOMPETITIVE,
        PriceStatus.NEEDS_CONFIG,
    }
    if result.status is PriceStatus.NEEDS_CONFIG:
        # A supplier cost too small for the configured margin floor must refuse,
        # never quote a price below the owner's floor.
        assert result.customer_price is None
        assert result.recommended is None
        assert result.gaps
        return
    if result.status is PriceStatus.UNCOMPETITIVE:
        assert result.floor is not None
        assert result.ceiling is not None
        assert result.customer_price is None
        return
    assert result.floor is not None
    assert result.ceiling is not None
    assert result.customer_price is not None
    assert result.floor.paise <= result.customer_price.paise
    assert result.customer_price.paise <= result.ceiling.paise
    assert result.customer_price.paise % 100 == 0
    assert sum(line.amount.paise for line in result.lines) == result.customer_price.paise
