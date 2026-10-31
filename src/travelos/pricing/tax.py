"""Accountant-approved tax tables and exact GST resolution (task 015b1).

The table is data: a rate row exists for one product, one GST basis and an
optional value slab. A tax is only produced when exactly one approved row
matches, so a missing or ambiguous row becomes ``NEEDS_CONFIG``, never a silent
zero. TCS rates and thresholds are not modelled yet (see docs/BACKLOG.md); a
ruleset that enables TCS must not quote until that table exists.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StrictInt, model_validator

from travelos.core.errors import CurrencyMismatchError, ValidationError
from travelos.core.money import Money
from travelos.pricing.models import GstBasis, PricingProduct


class TaxStatus(StrEnum):
    OK = "ok"
    UNAPPROVED = "unapproved"
    NOT_CONFIGURED = "not_configured"
    AMBIGUOUS = "ambiguous"


class TaxRateRow(BaseModel):
    """One approved rate. ``max_base_paise`` is exclusive, ``min`` inclusive."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    product: PricingProduct
    basis: GstBasis
    rate_bps: Annotated[StrictInt, Field(ge=0, le=10_000)]
    min_base_paise: Annotated[StrictInt, Field(ge=0)] | None = None
    max_base_paise: Annotated[StrictInt, Field(gt=0)] | None = None
    reference: Annotated[str, Field(min_length=1, max_length=100)]

    @model_validator(mode="after")
    def _slab_and_basis(self) -> Self:
        if (
            self.min_base_paise is not None
            and self.max_base_paise is not None
            and self.min_base_paise >= self.max_base_paise
        ):
            raise ValueError("min_base_paise must be below max_base_paise")
        if self.basis is GstBasis.NOT_APPLICABLE and self.rate_bps != 0:
            raise ValueError("not_applicable rows must be zero-rated")
        return self

    def applies_to(self, base_paise: int) -> bool:
        lower = self.min_base_paise if self.min_base_paise is not None else 0
        if base_paise < lower:
            return False
        return self.max_base_paise is None or base_paise < self.max_base_paise


class TaxTable(BaseModel):
    """A versioned table approved by the accountant, not by an engineer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    version: Annotated[StrictInt, Field(ge=1)]
    currency: Literal["INR"] = "INR"
    approved_by_accountant: bool
    approved_on: date
    rows: Annotated[tuple[TaxRateRow, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _unique_references(self) -> Self:
        references = [row.reference for row in self.rows]
        if len(references) != len(set(references)):
            raise ValueError("tax row reference must be unique")
        return self

    @model_validator(mode="after")
    def _no_overlapping_slabs(self) -> Self:
        grouped: dict[tuple[PricingProduct, GstBasis], list[TaxRateRow]] = {}
        for row in self.rows:
            grouped.setdefault((row.product, row.basis), []).append(row)
        for rows in grouped.values():
            ordered = sorted(
                rows, key=lambda row: row.min_base_paise if row.min_base_paise is not None else 0
            )
            previous_max: int | None = None
            for index, row in enumerate(ordered):
                lower = row.min_base_paise if row.min_base_paise is not None else 0
                if index and (previous_max is None or lower < previous_max):
                    raise ValueError("tax rate slabs overlap")
                previous_max = row.max_base_paise
        return self


@dataclass(frozen=True, slots=True)
class TaxLine:
    """A computed tax, traceable to one row of one table version."""

    product: PricingProduct
    basis: GstBasis
    rate_bps: int
    base: Money
    tax: Money
    reference: str
    table_version: int


@dataclass(frozen=True, slots=True)
class TaxResolution:
    status: TaxStatus
    line: TaxLine | None = None
    detail: str = ""


def resolve_tax(
    table: TaxTable,
    *,
    basis: GstBasis,
    product: PricingProduct,
    base: Money,
) -> TaxResolution:
    """Resolve exactly one approved row, or explain why no tax can be computed."""

    if base.currency != table.currency:
        raise CurrencyMismatchError(f"tax base is {base.currency}, table is {table.currency}")
    if base.paise < 0:
        raise ValidationError("tax base must not be negative")
    if not table.approved_by_accountant:
        return TaxResolution(
            status=TaxStatus.UNAPPROVED,
            detail=f"tax table {table.version} is not approved by the accountant",
        )

    matches = [
        row
        for row in table.rows
        if row.product == product and row.basis == basis and row.applies_to(base.paise)
    ]
    if not matches:
        return TaxResolution(
            status=TaxStatus.NOT_CONFIGURED,
            detail=(
                f"no approved tax row for {product.value}/{basis.value} at base {base.paise} paise"
            ),
        )
    if len(matches) > 1:
        references = ", ".join(row.reference for row in matches)
        return TaxResolution(
            status=TaxStatus.AMBIGUOUS,
            detail=f"multiple tax rows match {product.value}/{basis.value}: {references}",
        )

    row = matches[0]
    return TaxResolution(
        status=TaxStatus.OK,
        line=TaxLine(
            product=product,
            basis=basis,
            rate_bps=row.rate_bps,
            base=base,
            tax=base.apply_basis_points(row.rate_bps),
            reference=row.reference,
            table_version=table.version,
        ),
    )
