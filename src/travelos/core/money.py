"""The only money type in TravelOS: integer paise plus ISO currency.

Floats are rejected everywhere. Pricing, tax and ledger code must use this
type; display formatting happens only at the edge.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Self

from travelos.core.errors import CurrencyMismatchError, ValidationError

_CURRENCY_PATTERN = re.compile(r"^[A-Z]{3}$")
_BASIS_POINTS = 10_000


@dataclass(frozen=True, slots=True)
class Money:
    """An exact amount. ``paise`` is the minor unit (1 INR = 100 paise)."""

    paise: int
    currency: str = "INR"

    def __post_init__(self) -> None:
        if isinstance(self.paise, bool) or not isinstance(self.paise, int):
            raise ValidationError("Money.paise must be an integer")
        if not isinstance(self.currency, str) or not _CURRENCY_PATTERN.fullmatch(self.currency):
            raise ValidationError("Money.currency must be a 3-letter uppercase ISO code")

    @classmethod
    def zero(cls, currency: str = "INR") -> Self:
        return cls(0, currency)

    @classmethod
    def from_dict(cls, value: dict[str, object]) -> Self:
        paise = value.get("paise")
        currency = value.get("currency", "INR")
        if not isinstance(paise, int) or isinstance(paise, bool):
            raise ValidationError("money dict requires integer 'paise'")
        if not isinstance(currency, str):
            raise ValidationError("money dict requires string 'currency'")
        return cls(paise, currency)

    def to_dict(self) -> dict[str, int | str]:
        return {"paise": self.paise, "currency": self.currency}

    def __add__(self, other: object) -> Self:
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_currency(other)
        return type(self)(self.paise + other.paise, self.currency)

    def __sub__(self, other: object) -> Self:
        if not isinstance(other, Money):
            return NotImplemented
        self._require_same_currency(other)
        return type(self)(self.paise - other.paise, self.currency)

    def __neg__(self) -> Self:
        return type(self)(-self.paise, self.currency)

    def __mul__(self, factor: object) -> Self:
        if isinstance(factor, bool) or not isinstance(factor, int):
            raise ValidationError("Money can only be multiplied by an integer")
        return type(self)(self.paise * factor, self.currency)

    __rmul__ = __mul__

    def apply_basis_points(self, basis_points: int) -> Self:
        """Multiply by a rate in basis points (100 bps = 1%), half-up."""

        if isinstance(basis_points, bool) or not isinstance(basis_points, int):
            raise ValidationError("basis points must be an integer")
        numerator = self.paise * basis_points
        quotient, remainder = divmod(abs(numerator), _BASIS_POINTS)
        if remainder * 2 >= _BASIS_POINTS:
            quotient += 1
        return type(self)(quotient if numerator >= 0 else -quotient, self.currency)

    def is_zero(self) -> bool:
        return self.paise == 0

    def is_negative(self) -> bool:
        return self.paise < 0

    def __bool__(self) -> bool:
        return self.paise != 0

    def format(self, *, with_symbol: bool = True) -> str:
        """Format for display only, e.g. ``₹1,234.50`` or ``USD 1,234.50``."""

        sign = "-" if self.paise < 0 else ""
        rupees, paise = divmod(abs(self.paise), 100)
        amount = f"{rupees:,}.{paise:02d}"
        if with_symbol and self.currency == "INR":
            return f"{sign}₹{amount}"
        return f"{sign}{self.currency} {amount}"

    def _require_same_currency(self, other: Money) -> None:
        if self.currency != other.currency:
            raise CurrencyMismatchError(f"cannot combine {self.currency} with {other.currency}")


def money(paise: int, currency: str = "INR") -> Money:
    """Short constructor used by services and tests."""

    return Money(paise, currency)
