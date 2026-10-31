"""Tax-table domain and resolution tests (task 015b1).

Rates are synthetic fixtures, not Indian tax advice: the real table is supplied
and approved by the agency's accountant. The tests pin what the code guarantees:
a tax is traceable to exactly one approved row, an absent or unapproved table
yields ``NEEDS_CONFIG`` rather than a silent zero, and no float is involved.
"""

from __future__ import annotations

from datetime import date

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError as PydanticValidationError

from travelos.core.errors import CurrencyMismatchError, ValidationError
from travelos.core.money import Money, money
from travelos.pricing.models import GstBasis, PricingProduct
from travelos.pricing.tax import (
    TaxLine,
    TaxRateRow,
    TaxStatus,
    TaxTable,
    resolve_tax,
)


def row(
    *,
    product: PricingProduct = PricingProduct.HOTEL,
    basis: GstBasis = GstBasis.COMMISSION,
    rate_bps: int = 500,
    min_base_paise: int | None = None,
    max_base_paise: int | None = None,
    reference: str = "synthetic-gst-500",
) -> TaxRateRow:
    return TaxRateRow(
        product=product,
        basis=basis,
        rate_bps=rate_bps,
        min_base_paise=min_base_paise,
        max_base_paise=max_base_paise,
        reference=reference,
    )


def table(*rows: TaxRateRow, approved: bool = True, version: int = 1) -> TaxTable:
    return TaxTable(
        version=version,
        approved_by_accountant=approved,
        approved_on=date(2026, 10, 30),
        rows=rows or (row(),),
    )


def test_a_matching_row_produces_a_traceable_line() -> None:
    resolution = resolve_tax(
        table(),
        basis=GstBasis.COMMISSION,
        product=PricingProduct.HOTEL,
        base=money(100_000),
    )

    assert resolution.status is TaxStatus.OK
    assert resolution.line == TaxLine(
        product=PricingProduct.HOTEL,
        basis=GstBasis.COMMISSION,
        rate_bps=500,
        base=money(100_000),
        tax=money(5_000),
        reference="synthetic-gst-500",
        table_version=1,
    )


def test_an_unapproved_table_is_not_usable() -> None:
    resolution = resolve_tax(
        table(approved=False),
        basis=GstBasis.COMMISSION,
        product=PricingProduct.HOTEL,
        base=money(100_000),
    )

    assert resolution.status is TaxStatus.UNAPPROVED
    assert resolution.line is None
    assert "accountant" in resolution.detail


def test_a_missing_row_is_needs_config_not_zero() -> None:
    resolution = resolve_tax(
        table(row(product=PricingProduct.FLIGHT_DOMESTIC)),
        basis=GstBasis.COMMISSION,
        product=PricingProduct.HOTEL,
        base=money(100_000),
    )

    assert resolution.status is TaxStatus.NOT_CONFIGURED
    assert resolution.line is None
    assert "hotel" in resolution.detail
    assert "commission" in resolution.detail


def test_a_mismatched_basis_is_not_silently_reused() -> None:
    resolution = resolve_tax(
        table(row(basis=GstBasis.PRINCIPAL)),
        basis=GstBasis.COMMISSION,
        product=PricingProduct.HOTEL,
        base=money(100_000),
    )

    assert resolution.status is TaxStatus.NOT_CONFIGURED


def test_a_not_applicable_product_needs_an_explicit_zero_row() -> None:
    assert (
        resolve_tax(
            table(row(product=PricingProduct.VISA)),
            basis=GstBasis.NOT_APPLICABLE,
            product=PricingProduct.VISA,
            base=money(100_000),
        ).status
        is TaxStatus.NOT_CONFIGURED
    )

    resolution = resolve_tax(
        table(
            row(
                product=PricingProduct.VISA,
                basis=GstBasis.NOT_APPLICABLE,
                rate_bps=0,
                reference="synthetic-visa-exempt",
            )
        ),
        basis=GstBasis.NOT_APPLICABLE,
        product=PricingProduct.VISA,
        base=money(100_000),
    )

    assert resolution.status is TaxStatus.OK
    assert resolution.line is not None
    assert resolution.line.tax == money(0)
    assert resolution.line.reference == "synthetic-visa-exempt"


def test_value_slabs_select_exactly_one_row() -> None:
    slabs = table(
        row(min_base_paise=0, max_base_paise=500_000, reference="under-5000"),
        row(min_base_paise=500_000, reference="at-or-above-5000", rate_bps=1_800),
    )

    below = resolve_tax(
        slabs, basis=GstBasis.COMMISSION, product=PricingProduct.HOTEL, base=money(499_999)
    )
    at = resolve_tax(
        slabs, basis=GstBasis.COMMISSION, product=PricingProduct.HOTEL, base=money(500_000)
    )

    assert below.line is not None and below.line.reference == "under-5000"
    assert at.line is not None and at.line.reference == "at-or-above-5000"


def test_overlapping_slabs_are_refused_when_the_table_is_built() -> None:
    with pytest.raises(PydanticValidationError, match="overlap"):
        table(
            row(min_base_paise=0, max_base_paise=500_000, reference="first"),
            row(min_base_paise=400_000, reference="second"),
        )


def test_duplicate_references_are_refused() -> None:
    with pytest.raises(PydanticValidationError, match="reference"):
        table(
            row(reference="same"),
            row(rate_bps=1_800, reference="same"),
        )


def test_a_not_applicable_row_must_be_zero_rated() -> None:
    with pytest.raises(PydanticValidationError, match="not_applicable"):
        row(basis=GstBasis.NOT_APPLICABLE, rate_bps=500)


def test_a_negative_or_mixed_currency_base_is_refused() -> None:
    with pytest.raises(CurrencyMismatchError):
        resolve_tax(
            table(),
            basis=GstBasis.COMMISSION,
            product=PricingProduct.HOTEL,
            base=money(100_000, "USD"),
        )
    with pytest.raises(ValidationError, match="negative"):
        resolve_tax(
            table(),
            basis=GstBasis.COMMISSION,
            product=PricingProduct.HOTEL,
            base=money(-1),
        )


def test_the_table_requires_an_approval_date_and_a_row() -> None:
    with pytest.raises(PydanticValidationError):
        TaxTable(
            version=1,
            approved_by_accountant=True,
            approved_on=date(2026, 10, 30),
            rows=(),
        )


@given(st.integers(min_value=0, max_value=10**10), st.integers(min_value=0, max_value=10_000))
def test_tax_is_non_negative_and_matches_the_rate(base: int, rate_bps: int) -> None:
    row_ = row(rate_bps=rate_bps)

    resolution = resolve_tax(
        table(row_),
        basis=GstBasis.COMMISSION,
        product=PricingProduct.HOTEL,
        base=money(base),
    )

    assert resolution.line is not None
    tax = resolution.line.tax
    assert 0 <= tax.paise <= base
    assert tax == Money(base).apply_basis_points(rate_bps)


@given(st.integers(min_value=0, max_value=10**9))
def test_tax_is_monotonic_within_one_slab(base: int) -> None:
    resolution = resolve_tax(
        table(row(rate_bps=500)),
        basis=GstBasis.COMMISSION,
        product=PricingProduct.HOTEL,
        base=money(base),
    )
    higher = resolve_tax(
        table(row(rate_bps=500)),
        basis=GstBasis.COMMISSION,
        product=PricingProduct.HOTEL,
        base=money(base + 1),
    )

    assert resolution.line is not None
    assert higher.line is not None
    assert resolution.line.tax.paise <= higher.line.tax.paise
