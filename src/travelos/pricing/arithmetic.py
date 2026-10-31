"""Exact pricing arithmetic: FX conversion, rounding and margin bands (015b1).

All calculations use integer paise. An FX snapshot is an exact rational
(``numerator / denominator`` quote units per base unit) plus a buffer in basis
points; conversion rounds half-up once. A margin percentage always applies to
the supplier total, and the owner's ``margin_combination`` decides whether the
fixed ``min_paise`` is a floor (``max``) or an addition (``sum``). No choice is
defaulted here: an incomplete rule file never reaches this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt

from travelos.core.errors import CurrencyMismatchError, ValidationError
from travelos.core.money import Money
from travelos.pricing.models import (
    MarginCombination,
    MarginRule,
    RoundingMode,
    RoundingRule,
)

_BASIS_POINTS = 10_000
_CURRENCY_PATTERN = r"^[A-Z]{3}$"


class MarginProblem(StrEnum):
    """A margin rule that cannot be satisfied at this supplier total."""

    FLOOR_ABOVE_CAP = "floor_above_cap"


@dataclass(frozen=True, slots=True)
class MarginBand:
    """The minimum, target and capped margin for one supplier total."""

    minimum: Money
    target: Money
    cap: Money


class FxSnapshot(BaseModel):
    """An exact FX rate observed at a point in time.

    ``numerator / denominator`` is the number of quote-currency major units per
    one base-currency major unit (for example 83.25 INR per USD = 8325/100).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    base_currency: Annotated[str, Field(pattern=_CURRENCY_PATTERN)]
    quote_currency: Annotated[str, Field(pattern=_CURRENCY_PATTERN)]
    numerator: Annotated[StrictInt, Field(gt=0)]
    denominator: Annotated[StrictInt, Field(gt=0)]
    fetched_at: AwareDatetime
    source: Annotated[str, Field(min_length=1, max_length=200)]


def convert(amount: Money, snapshot: FxSnapshot, *, buffer_bps: int = 0) -> Money:
    """Convert ``amount`` into the snapshot's quote currency, half-up."""

    _require_basis_points(buffer_bps, "forex buffer")
    if amount.currency != snapshot.base_currency:
        raise CurrencyMismatchError(
            f"fx snapshot converts {snapshot.base_currency}, not {amount.currency}"
        )
    numerator = amount.paise * snapshot.numerator * (_BASIS_POINTS + buffer_bps)
    denominator = snapshot.denominator * _BASIS_POINTS
    return Money(_divide_half_up(numerator, denominator), snapshot.quote_currency)


def margin_band(
    supplier_total: Money,
    rule: MarginRule,
    combination: MarginCombination,
) -> MarginBand | MarginProblem:
    """Return the margin band for one supplier total.

    ``MAX`` treats ``min_paise`` as a floor on the percentage amount; ``SUM``
    adds it. A floor above the percentage cap is returned as a problem rather
    than clamped, because silently choosing one would change the customer price.
    """

    if supplier_total.paise < 0:
        raise ValidationError("supplier total must not be negative")
    currency = supplier_total.currency
    minimum_percent = supplier_total.apply_basis_points(rule.min_bps).paise
    target_percent = supplier_total.apply_basis_points(rule.target_bps).paise
    cap_percent = supplier_total.apply_basis_points(rule.max_bps).paise

    if combination is MarginCombination.SUM:
        minimum = rule.min_paise + minimum_percent
        target = rule.min_paise + target_percent
        cap = rule.min_paise + cap_percent
    elif combination is MarginCombination.MAX:
        minimum = max(rule.min_paise, minimum_percent)
        cap = cap_percent
        if cap < minimum:
            return MarginProblem.FLOOR_ABOVE_CAP
        target = max(target_percent, minimum)
    else:  # pragma: no cover - StrEnum makes this unreachable from validated data
        raise ValidationError(f"unknown margin combination: {combination}")

    return MarginBand(
        minimum=Money(minimum, currency),
        target=Money(target, currency),
        cap=Money(cap, currency),
    )


def round_money(amount: Money, rule: RoundingRule) -> Money:
    """Round to a step. ``up`` moves away from zero, ``down`` towards zero."""

    step = rule.step_paise
    quotient, remainder = divmod(abs(amount.paise), step)
    if remainder:
        if rule.mode is RoundingMode.NEAREST:
            if remainder * 2 >= step:
                quotient += 1
        elif rule.mode is RoundingMode.UP:
            quotient += 1
    rounded = quotient * step
    return Money(rounded if amount.paise >= 0 else -rounded, amount.currency)


def round_not_below(amount: Money, floor: Money, rule: RoundingRule) -> Money:
    """Round ``amount`` but never below ``floor`` (a price floor is absolute)."""

    if amount.currency != floor.currency:
        raise CurrencyMismatchError(f"cannot compare {amount.currency} with {floor.currency}")
    if amount.paise < 0 or floor.paise < 0:
        raise ValidationError("prices must not be negative")
    rounded = round_money(amount, rule)
    if rounded.paise >= floor.paise:
        return rounded
    return round_money(floor, RoundingRule(mode=RoundingMode.UP, step_paise=rule.step_paise))


def _require_basis_points(value: int, name: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValidationError(f"{name} must be an integer number of basis points")
    if not 0 <= value <= _BASIS_POINTS:
        raise ValidationError(f"{name} must be between 0 and {_BASIS_POINTS} bps")


def _divide_half_up(numerator: int, denominator: int) -> int:
    if denominator <= 0:  # pragma: no cover - callers guarantee a positive rate
        raise ValidationError("division denominator must be positive")
    sign = -1 if numerator < 0 else 1
    quotient, remainder = divmod(abs(numerator), denominator)
    if remainder * 2 >= denominator:
        quotient += 1
    return sign * quotient
