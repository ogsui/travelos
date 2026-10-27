"""Competitor price observations and comparability results (task 014a1).

A competitor page reports a retail price for one product: a route or city, a
travel window, a party, a fare class, a baggage allowance and a refundability
flag. Two prices are comparable only when those attributes line up, so the
normalized shape lives here rather than reusing the supplier ``Offer`` (a
competitor price has no supplier, availability or hold deadline).

No network, portal, database or LLM access is allowed in this module, and no
currency conversion happens: an observation in another currency is refused
rather than silently mixed into a total.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
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
CurrencyCode = Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
Paise = Annotated[StrictInt, Field(ge=0)]
Travellers = Annotated[StrictInt, Field(ge=0, le=30)]


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDateTime = Annotated[AwareDatetime, AfterValidator(_as_utc)]


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )


class ObservationStatus(StrEnum):
    """What the portal returned. Only ``ok`` carries a usable price."""

    OK = "ok"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"


class ComparabilityAttribute(StrEnum):
    ROUTE = "route"
    DATES = "dates"
    PAX = "pax"
    FARE_CLASS = "fare_class"
    BAGGAGE = "baggage"
    REFUNDABILITY = "refundability"


class ComparabilityLevel(StrEnum):
    """How closely one attribute matches.

    ``UNKNOWN`` means at least one side did not state it. Unknown never counts
    as a match; the policy caps how much unknown detail may still be trusted.
    """

    EXACT = "exact"
    CLOSE = "close"
    MISMATCH = "mismatch"
    UNKNOWN = "unknown"


class ComparisonRejection(StrEnum):
    """Why an observation cannot influence the price ceiling."""

    PRODUCT_TYPE = "product_type_mismatch"
    CURRENCY = "currency_mismatch"
    STALE = "stale"
    BLOCKED = "blocked"
    UNAVAILABLE = "unavailable"
    ROUTE = "route_mismatch"
    DATES = "dates_mismatch"
    PAX = "pax_mismatch"
    FARE_CLASS = "fare_class_mismatch"
    REFUNDABILITY = "refundability_mismatch"
    INSUFFICIENT_DETAIL = "insufficient_detail"
    BELOW_THRESHOLD = "below_threshold"


class ComparisonWarning(StrEnum):
    """Facts the pricing engine must explain when it has no trusted price."""

    NO_TRUSTED_PRICES = "no_trusted_prices"
    SINGLE_SOURCE = "single_source"
    OUTLIERS_IGNORED = "outliers_ignored"


class _ProductShape(_FrozenModel):
    """The attributes two prices must share to describe the same product."""

    route: Annotated[
        str,
        Field(
            min_length=2,
            max_length=200,
            description="Route for transport, city for a stay",
        ),
    ]
    start_date: date
    end_date: date | None = None
    adults: Annotated[StrictInt, Field(ge=1, le=30)] = 1
    children: Travellers = 0
    infants: Travellers = 0
    fare_class: Annotated[str | None, Field(max_length=40)] = None
    checked_bags: Annotated[StrictInt | None, Field(ge=0, le=10)] = None
    refundable: bool | None = None

    @field_validator("fare_class", mode="before")
    @classmethod
    def _blank_fare_class_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _dates_ordered(self) -> Self:
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        return self


class ComparabilityBasis(_ProductShape):
    """The product we are pricing, which observations are compared against."""

    product_type: ProductType
    currency: CurrencyCode = "INR"


class CompetitorPrice(_ProductShape):
    """One retail price read from one competitor page at one time."""

    site: Annotated[str, Field(min_length=2, max_length=120)]
    product_type: ProductType
    price_paise: Paise
    currency: CurrencyCode = "INR"
    fetched_at: UtcDateTime
    status: ObservationStatus = ObservationStatus.OK
    source_ref: Annotated[
        str | None,
        Field(
            max_length=500,
            description="Page or fixture reference; never a credential, cookie or token",
        ),
    ] = None

    @property
    def price(self) -> Money:
        return Money(self.price_paise, self.currency)


class AttributeComparison(_FrozenModel):
    """One scored attribute, with the templated reason shown to staff."""

    attribute: ComparabilityAttribute
    level: ComparabilityLevel
    detail: Annotated[str, Field(min_length=1, max_length=300)]


class ComparabilityAssessment(_FrozenModel):
    """Whether one observation may be trusted, and why."""

    score: Annotated[int, Field(ge=0, le=100)]
    comparable: bool
    threshold: Annotated[int, Field(ge=0, le=100)]
    unknown_weight: Annotated[int, Field(ge=0, le=100)]
    attributes: tuple[AttributeComparison, ...]
    rejection: ComparisonRejection | None
    detail: Annotated[str, Field(min_length=1, max_length=300)]

    @model_validator(mode="after")
    def _rejection_explains_refusal(self) -> Self:
        if self.comparable and self.rejection is not None:
            raise ValueError("a comparable observation cannot carry a rejection")
        if not self.comparable and self.rejection is None:
            raise ValueError("a refused observation needs a rejection reason")
        return self


class ComparabilityWeights(_FrozenModel):
    """How many of the 100 points each attribute is worth.

    ``route``, ``dates``, ``pax``, ``fare_class`` and ``refundability`` are
    identity dimensions: a mismatch refuses the observation outright. ``dates``
    and ``baggage`` may also score ``close``, so their weights decide how much
    partial credit an observation keeps.
    """

    route: Annotated[int, Field(ge=0, le=100)] = 30
    dates: Annotated[int, Field(ge=0, le=100)] = 25
    pax: Annotated[int, Field(ge=0, le=100)] = 15
    fare_class: Annotated[int, Field(ge=0, le=100)] = 10
    baggage: Annotated[int, Field(ge=0, le=100)] = 10
    refundability: Annotated[int, Field(ge=0, le=100)] = 10

    @model_validator(mode="after")
    def _totals_one_hundred(self) -> Self:
        total = (
            self.route + self.dates + self.pax + self.fare_class + self.baggage + self.refundability
        )
        if total != 100:
            raise ValueError("comparability weights must total 100")
        return self

    def weight(self, attribute: ComparabilityAttribute) -> int:
        """The points one attribute contributes when it matches exactly."""

        return int(getattr(self, attribute.value))


class ComparisonPolicy(_FrozenModel):
    """Tunable comparison rules.

    The defaults are engineering safety values, not owner policy. They are
    listed in docs/BACKLOG.md for confirmation before quotes rely on them.
    """

    threshold: Annotated[int, Field(ge=0, le=100)] = 80
    max_unknown_weight: Annotated[int, Field(ge=0, le=100)] = 20
    date_tolerance_days: Annotated[int, Field(ge=0, le=14)] = 1
    max_age: timedelta = timedelta(hours=24)
    mad_multiplier: Annotated[int, Field(ge=1, le=10)] = 3
    relative_outlier_floor_bps: Annotated[int, Field(ge=0, le=10_000)] = 1_000
    weights: ComparabilityWeights = Field(default_factory=ComparabilityWeights)

    @model_validator(mode="after")
    def _sane_age(self) -> Self:
        if self.max_age <= timedelta(0):
            raise ValueError("max_age must be positive")
        return self
