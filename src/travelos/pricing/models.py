"""Pricing rules, margins and tax references (task 015a).

Two layers of models:

* ``Raw*`` mirrors ``config/business_rules.yaml``. Every owner value is optional
  because a placeholder is a legitimate state; a missing value becomes a reported
  configuration gap, never a default.
* The domain models (``PricingRuleSet`` and friends) are complete and use integer
  basis points (100 bps = 1%), so the pricing calculation never sees a float.

Tax rates are not here: the file only names the accountant-approved table version
and the GST basis per product. The table itself is loaded separately (015c).
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Annotated, Literal, Self

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictInt,
    field_validator,
    model_validator,
)


class PricingProduct(StrEnum):
    """The product keys the owner prices. Matches the business-rule example."""

    FLIGHT_DOMESTIC = "flight_domestic"
    FLIGHT_INTERNATIONAL = "flight_international"
    HOTEL = "hotel"
    PACKAGE_FAMILY = "package_family"
    PACKAGE_CORPORATE = "package_corporate"
    PACKAGE_GROUP = "package_group"
    TRANSFER = "transfer"
    ACTIVITY = "activity"
    VISA = "visa"


class CustomerSegment(StrEnum):
    NEW = "new"
    REPEAT = "repeat"
    CORPORATE = "corporate"
    REFERRAL = "referral"
    GROUP = "group"


class RoundingMode(StrEnum):
    NEAREST = "nearest"
    UP = "up"
    DOWN = "down"


class GatewayFeePolicy(StrEnum):
    PASSTHROUGH = "passthrough"
    ABSORB = "absorb"


class GstBasis(StrEnum):
    """How GST applies to this product. ``not_applicable`` must be explicit."""

    COMMISSION = "commission"
    PRINCIPAL = "principal"
    NOT_APPLICABLE = "not_applicable"


class RuleStatus(StrEnum):
    OK = "ok"
    NEEDS_CONFIG = "needs_config"


def _percent_from_number(value: object) -> object:
    """Keep a YAML float such as 8.55 exact before Decimal validation."""

    if isinstance(value, float):
        return Decimal(str(value))
    return value


Percent = Annotated[
    Decimal,
    BeforeValidator(_percent_from_number),
    Field(ge=0, le=100, decimal_places=2),
]


class _RootModel(BaseModel):
    """Unknown top-level sections belong to other modules and are ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore", str_strip_whitespace=True)


class _StrictModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


# ---------------------------------------------------------------------------
# Raw configuration (placeholder-friendly)
# ---------------------------------------------------------------------------


class RawMarginRule(_StrictModel):
    min_paise: Annotated[StrictInt, Field(ge=0)] | None = None
    min_percent: Percent | None = None
    target_percent: Percent | None = None
    max_percent: Percent | None = None


class RawRounding(_StrictModel):
    mode: RoundingMode | None = None
    step_paise: Annotated[StrictInt, Field(ge=1, le=100_000)] | None = None


class RawRevalidationTolerance(_StrictModel):
    absolute_paise: Annotated[StrictInt, Field(ge=0)] | None = None
    percent: Percent | None = None


class RawCompetitorPolicy(_StrictModel):
    max_age_minutes: Annotated[StrictInt, Field(ge=1)] | None = None
    min_comparability: Annotated[StrictInt, Field(ge=0, le=100)] | None = None
    undercut_paise: Annotated[StrictInt, Field(ge=0)] | None = None


class RawPricingSection(_StrictModel):
    currency: str | None = None
    rounding: RawRounding = Field(default_factory=RawRounding)
    quote_validity_hours: Annotated[StrictInt, Field(ge=1, le=24 * 365)] | None = None
    revalidation_tolerance: RawRevalidationTolerance = Field(
        default_factory=RawRevalidationTolerance
    )
    forex_buffer_percent: Percent | None = None
    gateway_fee_policy: GatewayFeePolicy | None = None
    margins: dict[PricingProduct, RawMarginRule] = Field(default_factory=dict)
    segments_discount_budget_percent: dict[CustomerSegment, Percent | None] = Field(
        default_factory=dict
    )
    competitor: RawCompetitorPolicy = Field(default_factory=RawCompetitorPolicy)


class RawTcs(_StrictModel):
    enabled: bool | None = None
    rules_ref: Annotated[str, Field(max_length=200)] | None = None


class RawTaxSection(_StrictModel):
    tax_table_version: Annotated[StrictInt, Field(ge=1)] | None = None
    gst_basis_by_product: dict[PricingProduct, GstBasis] = Field(default_factory=dict)
    tcs: RawTcs = Field(default_factory=RawTcs)


class RawBusinessRules(_RootModel):
    version: Annotated[StrictInt, Field(ge=1)] | None = None
    pricing: RawPricingSection | None = None
    tax: RawTaxSection | None = None


# ---------------------------------------------------------------------------
# Complete domain rules (no placeholders)
# ---------------------------------------------------------------------------


class MarginRule(_StrictModel):
    """One product's margin boundary. Percentages are integer basis points.

    How ``min_paise`` combines with ``min_bps`` (a floor, a sum, the larger of the
    two) is task 015b and an owner decision; this model only fixes the values.
    """

    min_paise: Annotated[StrictInt, Field(ge=0)]
    min_bps: Annotated[StrictInt, Field(ge=0, le=10_000)]
    target_bps: Annotated[StrictInt, Field(ge=0, le=10_000)]
    max_bps: Annotated[StrictInt, Field(ge=0, le=10_000)]

    @model_validator(mode="after")
    def _ordered(self) -> Self:
        if not self.min_bps <= self.target_bps <= self.max_bps:
            raise ValueError("margin percentages must satisfy min <= target <= max")
        return self


class RoundingRule(_StrictModel):
    mode: RoundingMode
    step_paise: Annotated[StrictInt, Field(ge=1, le=100_000)]


class RevalidationTolerance(_StrictModel):
    absolute_paise: Annotated[StrictInt, Field(ge=0)]
    percent_bps: Annotated[StrictInt, Field(ge=0, le=10_000)]


class CompetitorPolicy(_StrictModel):
    max_age: timedelta
    min_comparability: Annotated[StrictInt, Field(ge=0, le=100)]
    undercut_paise: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def _positive_age(self) -> Self:
        if self.max_age <= timedelta(0):
            raise ValueError("competitor max_age must be positive")
        return self


class TcsPolicy(_StrictModel):
    enabled: bool
    rules_ref: Annotated[str, Field(max_length=200)] | None = None

    @field_validator("rules_ref", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @model_validator(mode="after")
    def _reference_when_enabled(self) -> Self:
        if self.enabled and not self.rules_ref:
            raise ValueError("TCS is enabled but rules_ref is missing")
        return self


class PricingRuleSet(_StrictModel):
    """A complete, owner-approved rule set loaded from one file version."""

    version: Annotated[StrictInt, Field(ge=1)]
    currency: Literal["INR"] = "INR"
    rounding: RoundingRule
    quote_validity_hours: Annotated[StrictInt, Field(ge=1, le=24 * 365)]
    revalidation: RevalidationTolerance
    forex_buffer_bps: Annotated[StrictInt, Field(ge=0, le=10_000)]
    gateway_fee_policy: GatewayFeePolicy
    margins: dict[PricingProduct, MarginRule]
    segment_discount_budget_bps: dict[CustomerSegment, Annotated[StrictInt, Field(ge=0, le=10_000)]]
    competitor: CompetitorPolicy
    tax_table_version: Annotated[StrictInt, Field(ge=1)]
    gst_basis_by_product: dict[PricingProduct, GstBasis]
    tcs: TcsPolicy

    @model_validator(mode="after")
    def _complete_for_every_product(self) -> Self:
        missing_margins = set(PricingProduct) - set(self.margins)
        if missing_margins:
            names = ", ".join(sorted(item.value for item in missing_margins))
            raise ValueError(f"missing margin rules: {names}")
        missing_bases = set(PricingProduct) - set(self.gst_basis_by_product)
        if missing_bases:
            names = ", ".join(sorted(item.value for item in missing_bases))
            raise ValueError(f"missing GST bases: {names}")
        missing_segments = set(CustomerSegment) - set(self.segment_discount_budget_bps)
        if missing_segments:
            names = ", ".join(sorted(item.value for item in missing_segments))
            raise ValueError(f"missing discount budgets: {names}")
        return self
