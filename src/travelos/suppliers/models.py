"""Normalized supplier domain models (task 012a1).

Every adapter returns these shapes, whatever the supplier calls them. The rules
that matter for the rest of TravelOS are enforced here:

* money is integer paise plus an ISO currency, never a float;
* timestamps are timezone-aware and normalized to UTC;
* an offer's expiry follows its fetch time and its fare rules share its currency;
* a hold cannot outlive its own creation, and a cancelled booking records when.

Raw provider payloads, portal page shapes and task-012b parsers stay outside this
module. In particular, nothing here interprets a fare rule into a customer-facing
cancellation policy: the owner's policy engine (task 015) does that.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    AfterValidator,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)

from travelos.core.money import Money

ProductType = Literal["flight", "hotel", "transfer", "activity", "visa"]
BookingState = Literal["confirmed", "cancelled"]


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


def _aware_utc(value: datetime, label: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")
    return value.astimezone(UTC)


UtcDateTime = Annotated[AwareDatetime, AfterValidator(_as_utc)]
CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Paise = Annotated[StrictInt, Field(ge=0)]
TravellerCount = Annotated[StrictInt, Field(ge=1, le=30)]
IdempotencyKey = Annotated[str, Field(min_length=8, max_length=200)]
ShortText = Annotated[str, Field(min_length=1, max_length=200)]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )


class CancellationRules(_FrozenModel):
    """The supplier's cancellation terms, reported but not interpreted.

    ``cancellation_days`` is the supplier's stated deadline window and
    ``change_fee_paise`` its stated change fee. Whether a customer may cancel, and
    on what terms, is an owner policy decision made elsewhere.
    """

    refundable: bool
    cancellation_days: Annotated[StrictInt, Field(ge=0, le=365)]
    change_fee_paise: Paise
    currency: CurrencyCode = "INR"

    @property
    def change_fee(self) -> Money:
        return Money(self.change_fee_paise, self.currency)


class FareRules(_FrozenModel):
    """Normalized fare conditions attached to an offer."""

    cancellation: CancellationRules

    @property
    def refundable(self) -> bool:
        return self.cancellation.refundable

    @property
    def change_fee(self) -> Money:
        return self.cancellation.change_fee

    def to_dict(self) -> dict[str, object]:
        """Flat shape used by snapshots and the synthetic fixture contract."""

        return {
            "refundable": self.cancellation.refundable,
            "cancellation_days": self.cancellation.cancellation_days,
            "change_fee_paise": self.cancellation.change_fee_paise,
            "currency": self.cancellation.currency,
        }


class Offer(_FrozenModel):
    """One sellable supplier option at one price and time.

    ``offer_id`` is the supplier's own opaque reference; it is unique within a
    supplier, not across suppliers. ``expires_at`` is the snapshot/sale TTL set
    by the adapter, while the travel window lives on the search result.
    """

    supplier: ShortText
    offer_id: ShortText
    product_type: ProductType
    name: Annotated[str, Field(min_length=1, max_length=300)]
    city: Annotated[str, Field(min_length=2, max_length=120)]
    route: Annotated[str | None, Field(max_length=200)] = None
    currency: CurrencyCode = "INR"
    net_paise: Paise
    taxes_paise: Paise
    available_count: Annotated[StrictInt, Field(ge=0)]
    fare_rules: FareRules
    fetched_at: UtcDateTime
    expires_at: UtcDateTime

    @field_validator("route", mode="before")
    @classmethod
    def _blank_route_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.fare_rules.cancellation.currency != self.currency:
            raise ValueError("fare rules currency must match the offer currency")
        if self.expires_at <= self.fetched_at:
            raise ValueError("expires_at must be after fetched_at")
        return self

    @property
    def net(self) -> Money:
        return Money(self.net_paise, self.currency)

    @property
    def taxes(self) -> Money:
        return Money(self.taxes_paise, self.currency)

    @property
    def total(self) -> Money:
        return Money(self.total_paise, self.currency)

    @property
    def total_paise(self) -> int:
        return self.net_paise + self.taxes_paise

    @property
    def is_available(self) -> bool:
        return self.available_count > 0

    def is_expired(self, now: datetime) -> bool:
        return _aware_utc(now, "now") >= self.expires_at


class SearchLeg(_FrozenModel):
    """One product request inside a supplier search.

    A leg needs a city or a route so a search is bounded; ``travel_date`` filters
    against the supplier's validity window when it is known.
    """

    product_type: ProductType
    city: Annotated[str | None, Field(min_length=2, max_length=120)] = None
    route: Annotated[str | None, Field(max_length=200)] = None
    travel_date: date | None = None

    @field_validator("city", "route", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _bounded(self) -> Self:
        if self.city is None and self.route is None:
            raise ValueError("a search leg needs a city or route")
        return self


class SupplierSearchQuery(_FrozenModel):
    """A bounded search request; one supplier may serve several legs."""

    legs: Annotated[tuple[SearchLeg, ...], Field(min_length=1, max_length=5)]
    max_results_per_leg: Annotated[StrictInt, Field(ge=1, le=50)] = 10

    @model_validator(mode="after")
    def _no_duplicate_legs(self) -> Self:
        if len(set(self.legs)) != len(self.legs):
            raise ValueError("duplicate search legs are not allowed")
        return self


class FailureCode(StrEnum):
    """Why a leg produced no offers."""

    TIMEOUT = "timeout"
    UNAVAILABLE = "unavailable"
    PARTIAL_FAILURE = "partial_failure"


class SearchFailure(_FrozenModel):
    """A leg-level failure inside an otherwise useful search result."""

    product_type: ProductType
    code: FailureCode
    message: Annotated[str, Field(min_length=1, max_length=500)]
    retryable: bool


class SearchResult(_FrozenModel):
    """A supplier's answer: offers plus any legs that failed.

    ``complete`` is false when at least one leg failed. The orchestrator (task
    012a2) decides whether the partial result is enough to continue; the adapter
    never hides a failed leg.
    """

    supplier: ShortText
    offers: tuple[Offer, ...] = ()
    failures: tuple[SearchFailure, ...] = ()
    fetched_at: UtcDateTime

    @model_validator(mode="after")
    def _offers_belong_to_the_supplier(self) -> Self:
        if any(offer.supplier != self.supplier for offer in self.offers):
            raise ValueError("every offer supplier must match the search result supplier")
        return self

    @property
    def complete(self) -> bool:
        return not self.failures

    @property
    def offer_total(self) -> int:
        return len(self.offers)


class HoldRequest(_FrozenModel):
    """Ask a supplier to hold a number of seats against one offer."""

    offer_id: ShortText
    traveller_count: TravellerCount = 1
    idempotency_key: IdempotencyKey


class Hold(_FrozenModel):
    """A supplier hold. ``expires_at=None`` means the supplier sets no deadline."""

    supplier: ShortText
    hold_id: ShortText
    offer: Offer
    traveller_count: TravellerCount
    created_at: UtcDateTime
    expires_at: UtcDateTime | None = None

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.offer.supplier != self.supplier:
            raise ValueError("hold supplier must match the offer supplier")
        if self.expires_at is not None and self.expires_at <= self.created_at:
            raise ValueError("hold expires_at must be after created_at")
        return self

    def is_expired(self, now: datetime) -> bool:
        if self.expires_at is None:
            return False
        return _aware_utc(now, "now") >= self.expires_at


class BookingRequest(_FrozenModel):
    """Turn a live hold into a booking.

    Only the booking orchestrator (task 020) may call this, after the approval
    engine has authorised the action. The adapter itself records the booking; it
    never moves money.
    """

    hold_id: ShortText
    idempotency_key: IdempotencyKey


class Booking(_FrozenModel):
    """A supplier booking, including the fare rules it was made under."""

    supplier: ShortText
    booking_ref: ShortText
    hold_id: ShortText
    offer_id: ShortText
    traveller_count: TravellerCount
    currency: CurrencyCode = "INR"
    net_paise: Paise
    taxes_paise: Paise
    fare_rules: FareRules
    state: BookingState
    booked_at: UtcDateTime
    cancelled_at: UtcDateTime | None = None

    @model_validator(mode="after")
    def _state_matches_cancelled_at(self) -> Self:
        if self.state == "cancelled" and self.cancelled_at is None:
            raise ValueError("a cancelled booking needs cancelled_at")
        if self.state == "confirmed" and self.cancelled_at is not None:
            raise ValueError("a confirmed booking cannot have cancelled_at")
        if self.cancelled_at is not None and self.cancelled_at < self.booked_at:
            raise ValueError("cancelled_at must not be before booked_at")
        if self.fare_rules.cancellation.currency != self.currency:
            raise ValueError("fare rules currency must match the booking currency")
        return self

    @property
    def net(self) -> Money:
        return Money(self.net_paise, self.currency)

    @property
    def taxes(self) -> Money:
        return Money(self.taxes_paise, self.currency)

    @property
    def total(self) -> Money:
        return Money(self.total_paise, self.currency)

    @property
    def total_paise(self) -> int:
        return self.net_paise + self.taxes_paise

    @property
    def is_active(self) -> bool:
        return self.state == "confirmed"


class CancelRequest(_FrozenModel):
    """Ask a supplier to cancel a booking.

    As with ``BookingRequest``, only the booking orchestrator may reach this after
    a human approval. ``reason`` is untrusted free text and is never interpreted
    as an instruction.
    """

    booking_ref: ShortText
    idempotency_key: IdempotencyKey
    reason: Annotated[str | None, Field(max_length=500)] = None


class CancellationResult(_FrozenModel):
    """The supplier's cancellation answer, including the reported money split."""

    booking_ref: ShortText
    state: Literal["cancelled"] = "cancelled"
    refund_paise: Paise
    fee_paise: Paise
    currency: CurrencyCode = "INR"
    cancelled_at: UtcDateTime

    @property
    def refund(self) -> Money:
        return Money(self.refund_paise, self.currency)

    @property
    def fee(self) -> Money:
        return Money(self.fee_paise, self.currency)


class WalletBalance(_FrozenModel):
    """Read-only supplier wallet/credit balance. Never a payment operation."""

    supplier: ShortText
    balance_paise: Paise
    currency: CurrencyCode = "INR"
    as_of: UtcDateTime

    @property
    def balance(self) -> Money:
        return Money(self.balance_paise, self.currency)
