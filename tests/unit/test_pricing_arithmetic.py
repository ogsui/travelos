"""Exact integer arithmetic for pricing: FX, rounding and margin bands (015b1).

Every amount is ``Money`` (integer paise). FX rates are exact rationals with a
separate forex buffer in basis points; rounding and margins never use a float.
Margin percentages always apply to the supplier total, and how the fixed
``min_paise`` combines with the percentage is the owner's configured choice
(``max`` or ``sum``), not an engineering default.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from travelos.core.errors import CurrencyMismatchError
from travelos.core.errors import ValidationError as DomainValidationError
from travelos.core.money import money
from travelos.pricing.arithmetic import (
    FxSnapshot,
    MarginBand,
    MarginProblem,
    convert,
    margin_band,
    round_money,
    round_not_below,
)
from travelos.pricing.models import (
    MarginCombination,
    MarginRule,
    RoundingMode,
    RoundingRule,
)


def rule(
    *,
    min_paise: int = 0,
    min_bps: int = 0,
    target_bps: int = 0,
    max_bps: int = 0,
) -> MarginRule:
    return MarginRule(
        min_paise=min_paise,
        min_bps=min_bps,
        target_bps=target_bps,
        max_bps=max_bps,
    )


def snapshot(
    *,
    base: str = "USD",
    quote: str = "INR",
    numerator: int = 8_325,
    denominator: int = 100,
) -> FxSnapshot:
    return FxSnapshot(
        base_currency=base,
        quote_currency=quote,
        numerator=numerator,
        denominator=denominator,
        fetched_at=datetime(2026, 10, 30, 12, 0, tzinfo=UTC),
        source="synthetic test rate",
    )


# ---------------------------------------------------------------------------
# FX
# ---------------------------------------------------------------------------


def test_fx_rate_is_an_exact_rational_with_a_buffer() -> None:
    # 83.25 INR per USD, USD 100.00 = 10000 cents.
    assert convert(money(10_000, "USD"), snapshot()) == money(832_500)
    # 1.5% buffer: 832500 * 10150 / 10000 = 844987.5 -> half-up.
    assert convert(money(10_000, "USD"), snapshot(), buffer_bps=150) == money(844_988)


def test_fx_rejects_a_currency_mismatch() -> None:
    with pytest.raises(CurrencyMismatchError, match="INR"):
        convert(money(10_000), snapshot())


def test_fx_rejects_an_invalid_buffer() -> None:
    with pytest.raises(DomainValidationError, match="buffer"):
        convert(money(10_000, "USD"), snapshot(), buffer_bps=-1)
    with pytest.raises(DomainValidationError, match="buffer"):
        convert(money(10_000, "USD"), snapshot(), buffer_bps=10_001)
    with pytest.raises(DomainValidationError, match="buffer"):
        convert(money(10_000, "USD"), snapshot(), buffer_bps=1.5)  # type: ignore[arg-type]


def test_fx_snapshot_validation() -> None:
    base = dict(
        base_currency="USD",
        quote_currency="INR",
        numerator=1,
        denominator=1,
        fetched_at=datetime(2026, 10, 30, tzinfo=UTC),
        source="test",
    )
    with pytest.raises(ValidationError):
        FxSnapshot(**{**base, "numerator": 0})
    with pytest.raises(ValidationError):
        FxSnapshot(**{**base, "denominator": -1})
    with pytest.raises(ValidationError):
        FxSnapshot(**{**base, "base_currency": "usd"})
    with pytest.raises(ValidationError):
        FxSnapshot(**{**base, "fetched_at": datetime(2026, 10, 30)})
    with pytest.raises(ValidationError):
        FxSnapshot(**{**base, "source": ""})
    with pytest.raises(ValidationError):
        FxSnapshot(**{**base, "extra": 1})


@given(st.integers(min_value=0, max_value=10**12))
def test_fx_identity_keeps_the_amount(paise: int) -> None:
    assert convert(money(paise, "USD"), snapshot(numerator=1, denominator=1)) == money(paise)


@given(
    st.integers(min_value=0, max_value=10**10),
    st.integers(min_value=0, max_value=5_000),
)
def test_fx_is_monotonic_and_never_negative(paise: int, buffer_bps: int) -> None:
    lower = convert(money(paise, "USD"), snapshot(), buffer_bps=buffer_bps)
    higher = convert(money(paise + 1, "USD"), snapshot(), buffer_bps=buffer_bps)

    assert 0 <= lower.paise <= higher.paise


def test_fx_result_is_within_half_a_paise_of_the_exact_rational() -> None:
    from fractions import Fraction

    amount = money(999_999, "USD")
    rate = snapshot(numerator=8_325, denominator=100)

    converted = convert(amount, rate, buffer_bps=75)
    exact = Fraction(amount.paise * rate.numerator * 10_075, rate.denominator * 10_000)

    assert abs(Fraction(converted.paise) - exact) <= Fraction(1, 2)


# ---------------------------------------------------------------------------
# Rounding
# ---------------------------------------------------------------------------


def test_rounding_modes() -> None:
    nearest = RoundingRule(mode=RoundingMode.NEAREST, step_paise=100)
    up = RoundingRule(mode=RoundingMode.UP, step_paise=100)
    down = RoundingRule(mode=RoundingMode.DOWN, step_paise=100)

    assert round_money(money(1_234), nearest) == money(1_200)
    assert round_money(money(1_250), nearest) == money(1_300)  # half-up
    assert round_money(money(1_201), up) == money(1_300)
    assert round_money(money(1_299), down) == money(1_200)
    assert round_money(money(1_200), nearest) == money(1_200)
    assert round_money(money(0), nearest) == money(0)


def test_round_not_below_never_crosses_the_floor() -> None:
    rule_nearest = RoundingRule(mode=RoundingMode.NEAREST, step_paise=100)

    assert round_not_below(money(1_234), money(1_250), rule_nearest) == money(1_300)
    assert round_not_below(money(1_260), money(1_250), rule_nearest) == money(1_300)
    assert round_not_below(money(1_240), money(1_200), rule_nearest) == money(1_200)


def test_rounding_rejects_mixed_currencies() -> None:
    with pytest.raises(CurrencyMismatchError):
        round_not_below(
            money(1_234), money(1_250, "USD"), RoundingRule(mode=RoundingMode.UP, step_paise=100)
        )


@given(st.integers(min_value=0, max_value=10**9), st.integers(min_value=1, max_value=10_000))
def test_rounding_is_a_multiple_of_the_step_and_within_one_step(paise: int, step: int) -> None:
    rounded = round_money(money(paise), RoundingRule(mode=RoundingMode.NEAREST, step_paise=step))

    assert rounded.paise % step == 0
    assert abs(rounded.paise - paise) <= step


# ---------------------------------------------------------------------------
# Margin bands
# ---------------------------------------------------------------------------


def test_max_combination_takes_the_larger_fixed_or_percentage_floor() -> None:
    band = margin_band(
        money(100_000),
        rule(min_paise=5_000, min_bps=300, target_bps=800, max_bps=1_500),
        MarginCombination.MAX,
    )

    assert band == MarginBand(
        minimum=money(5_000),
        target=money(8_000),
        cap=money(15_000),
    )


def test_sum_combination_adds_the_fixed_amount() -> None:
    band = margin_band(
        money(100_000),
        rule(min_paise=5_000, min_bps=300, target_bps=800, max_bps=1_500),
        MarginCombination.SUM,
    )

    assert band == MarginBand(
        minimum=money(8_000),
        target=money(13_000),
        cap=money(20_000),
    )


def test_a_floor_above_the_cap_is_reported_not_clamped() -> None:
    assert (
        margin_band(
            money(100_000),
            rule(min_paise=20_000, min_bps=300, target_bps=800, max_bps=1_500),
            MarginCombination.MAX,
        )
        is MarginProblem.FLOOR_ABOVE_CAP
    )


def test_the_target_is_lifted_to_the_floor_but_never_above_the_cap() -> None:
    band = margin_band(
        money(100_000),
        rule(min_paise=10_000, min_bps=300, target_bps=500, max_bps=1_500),
        MarginCombination.MAX,
    )

    assert isinstance(band, MarginBand)
    assert (band.minimum.paise, band.target.paise, band.cap.paise) == (10_000, 10_000, 15_000)


def test_margin_rejects_negative_supplier_totals() -> None:
    with pytest.raises(DomainValidationError, match="negative"):
        margin_band(money(-1), rule(), MarginCombination.MAX)


@given(
    st.integers(min_value=0, max_value=10**9),
    st.integers(min_value=0, max_value=50_000),
    st.integers(min_value=0, max_value=10_000),
    st.integers(min_value=0, max_value=10_000),
)
def test_margin_band_is_ordered_or_reports_a_problem(
    total: int, min_paise: int, first_bps: int, second_bps: int
) -> None:
    min_bps, target_bps, max_bps = sorted((first_bps, second_bps, 10_000))
    rule_ = rule(
        min_paise=min_paise,
        min_bps=min_bps,
        target_bps=target_bps,
        max_bps=max_bps,
    )

    for combination in MarginCombination:
        band = margin_band(money(total), rule_, combination)
        if isinstance(band, MarginBand):
            assert band.minimum.paise <= band.target.paise <= band.cap.paise
            assert band.minimum.paise >= rule_.min_paise
        else:
            assert combination is MarginCombination.MAX


def test_margin_is_monotonic_in_the_supplier_total() -> None:
    rule_ = rule(min_paise=5_000, min_bps=300, target_bps=800, max_bps=1_500)

    lower = margin_band(money(100_000), rule_, MarginCombination.SUM)
    higher = margin_band(money(200_000), rule_, MarginCombination.SUM)

    assert isinstance(lower, MarginBand)
    assert isinstance(higher, MarginBand)
    assert lower.minimum.paise <= higher.minimum.paise
    assert lower.target.paise <= higher.target.paise
    assert lower.cap.paise <= higher.cap.paise


# ---------------------------------------------------------------------------
# The module must stay float-free
# ---------------------------------------------------------------------------


def test_pricing_sources_contain_no_float_arithmetic() -> None:
    import ast
    from pathlib import Path

    root = Path(__file__).resolve().parents[2] / "src" / "travelos" / "pricing"
    for path in root.glob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            assert not (isinstance(node, ast.Constant) and isinstance(node.value, float)), path
            assert not isinstance(node, ast.Div), path
            assert not (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in {"float", "round"}
            ), path
