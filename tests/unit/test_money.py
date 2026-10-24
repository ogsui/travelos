"""Money value-type tests, including property tests and the model scan.

Pricing and ledger code must never use floats. ``test_no_float_money_columns``
scans the SQLAlchemy metadata so a regression fails in CI rather than in
production reports.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
from hypothesis import given
from hypothesis import strategies as st
from sqlalchemy import BigInteger, Float, Integer, Numeric

import travelos.core.models
import travelos.crm.models  # noqa: F401
from travelos.core.db import Base
from travelos.core.errors import CurrencyMismatchError, ValidationError
from travelos.core.money import Money, money


def test_money_holds_integer_paise() -> None:
    amount = money(123_450)

    assert amount.paise == 123_450
    assert amount.currency == "INR"


@pytest.mark.parametrize("bad", [1.5, "100", None, True, False, Decimal("1.00")])
def test_money_rejects_non_integer_paise(bad: object) -> None:
    with pytest.raises(ValidationError, match="integer"):
        Money(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", ["inr", "IN", "INRR", "", "₹"])
def test_money_rejects_invalid_currency(bad: str) -> None:
    with pytest.raises(ValidationError, match="ISO code"):
        Money(100, bad)


def test_money_add_subtract_and_negate() -> None:
    total = money(10_000) + money(2_500)

    assert total == Money(12_500)
    assert total - Money(2_500) == Money(10_000)
    assert -total == Money(-12_500)


def test_money_rejects_currency_mix() -> None:
    with pytest.raises(CurrencyMismatchError, match="INR with USD"):
        _ = Money(100) + Money(100, "USD")


def test_money_add_rejects_plain_numbers() -> None:
    with pytest.raises(TypeError):
        _ = Money(100) + 100  # type: ignore[operator]


def test_money_multiplies_by_integer_only() -> None:
    assert Money(2_999) * 3 == Money(8_997)
    assert 3 * Money(2_999) == Money(8_997)

    with pytest.raises(ValidationError, match="integer"):
        _ = Money(100) * 1.5  # type: ignore[operator]


@pytest.mark.parametrize(
    ("paise", "basis_points", "expected"),
    [
        (10_000, 500, 500),  # 5%
        (10_000, 0, 0),
        (10_000, 10_000, 10_000),
        (1, 5_000, 1),  # 0.5 paise rounds half up
        (1, 4_999, 0),
        (-1, 5_000, -1),
        (-1, 4_999, 0),
        (999, 125, 12),  # 12.4875 -> 12
        (999, 175, 17),  # 17.4825 -> 17
    ],
)
def test_money_basis_points_round_half_up(paise: int, basis_points: int, expected: int) -> None:
    assert Money(paise).apply_basis_points(basis_points) == Money(expected)


def test_money_formatting_is_display_only() -> None:
    assert Money(123_456).format() == "₹1,234.56"
    assert Money(-123_456).format() == "-₹1,234.56"
    assert Money(100, "USD").format() == "USD 1.00"
    assert Money(100, "USD").format(with_symbol=False) == "USD 1.00"
    assert Money(0).format() == "₹0.00"


def test_money_dict_roundtrip() -> None:
    amount = Money(987_654, "EUR")

    assert Money.from_dict(amount.to_dict()) == amount
    with pytest.raises(ValidationError):
        Money.from_dict({"paise": 1.5})


def test_money_is_hashable_and_truthy_by_value() -> None:
    assert len({Money(0), Money(0), Money(1)}) == 2
    assert not Money(0)
    assert Money(-1)
    assert Money(0).is_zero()
    assert Money(-1).is_negative()


@given(st.integers(-(10**12), 10**12))
def test_zero_is_the_additive_identity(paise: int) -> None:
    amount = Money(paise)
    assert amount + Money.zero() == amount
    assert amount - amount == Money.zero()


@given(st.integers(-(10**9), 10**9), st.integers(-(10**9), 10**9))
def test_add_then_subtract_is_reversible(left: int, right: int) -> None:
    assert (Money(left) + Money(right)) - Money(right) == Money(left)


@given(st.integers(-(10**9), 10**9), st.integers(-1_000, 1_000))
def test_multiplication_matches_repeated_addition(paise: int, factor: int) -> None:
    assert Money(paise) * factor == Money(paise * factor)


@given(st.integers(-(10**9), 10**9))
def test_full_basis_points_are_a_no_op(paise: int) -> None:
    assert Money(paise).apply_basis_points(10_000) == Money(paise)


def test_no_float_or_numeric_columns_in_any_model() -> None:
    offenders = [
        f"{table.name}.{column.name} ({column.type})"
        for table in Base.metadata.tables.values()
        for column in table.columns
        if isinstance(column.type, Float | Numeric)
    ]

    assert offenders == []


def test_every_money_column_is_an_integer_paise_column() -> None:
    money_columns = {
        (table.name, column.name): column.type
        for table in Base.metadata.tables.values()
        for column in table.columns
        if column.name.endswith("_paise")
    }

    assert ("customers", "lifetime_value_paise") in money_columns
    for name, column_type in money_columns.items():
        assert isinstance(column_type, BigInteger | Integer), f"{name} is {column_type}"


def test_every_paise_column_has_a_currency_partner() -> None:
    for table in Base.metadata.tables.values():
        names = set(table.columns.keys())
        for name in names:
            if name.endswith("_paise"):
                stem = name.removesuffix("_paise")
                assert f"{stem}_currency" in names or "currency" in names, (
                    f"{table.name}.{name} has no currency column"
                )
