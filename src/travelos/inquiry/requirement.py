"""The parsed travel requirement and its evidence (task 010a).

Field names, ranges and enums mirror ``data/schemas/inquiry.schema.json``
``$defs.requirement``. That schema is the shared contract with the synthetic
generator and the parser eval, so a change here needs the schema and gold data
updated in the same commit; ``tests/unit/test_inquiry_requirement.py`` fails on
drift.

A ``Requirement`` is deliberately allowed to be incomplete: the parser returns
what the customer actually said, and ``inquiry.rules`` decides what is still
missing. Neutral defaults (``infants=0``, ``urgency="normal"``,
``budget_currency="INR"``) mean "not stated", never a guess about the customer.
This module contains no LLM, network or database access.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum
from typing import Annotated, Final, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from travelos.core.money import Money
from travelos.inquiry.errors import SilentAssumptionError

TripType = Literal["flight", "package", "hotel", "visa", "corporate", "group", "unknown"]
Urgency = Literal["low", "normal", "high"]

_MAX_CITY_LENGTH: Final = 120
_MAX_TAG_LENGTH: Final = 100
_MAX_TAGS: Final = 20


class RequirementField(StrEnum):
    """Canonical field names. The values are part of the JSON schema contract."""

    TRIP_TYPE = "trip_type"
    ORIGIN_CITY = "origin_city"
    DESTINATIONS = "destinations"
    START_DATE = "start_date"
    END_DATE = "end_date"
    DATE_FLEXIBILITY_DAYS = "date_flexibility_days"
    ADULTS = "adults"
    CHILDREN_AGES = "children_ages"
    INFANTS = "infants"
    ROOMS = "rooms"
    BUDGET_PAISE = "budget_paise"
    BUDGET_CURRENCY = "budget_currency"
    PREFERENCES = "preferences"
    NATIONALITY = "nationality"
    SPECIAL_NEEDS = "special_needs"
    URGENCY = "urgency"


REQUIREMENT_FIELDS: Final[tuple[str, ...]] = tuple(field.value for field in RequirementField)

_NEUTRAL_LISTS: Final = frozenset(
    {
        RequirementField.DESTINATIONS,
        RequirementField.CHILDREN_AGES,
        RequirementField.PREFERENCES,
        RequirementField.SPECIAL_NEEDS,
    }
)


class SourceSpan(BaseModel):
    """A half-open ``[start, end)`` character range in the original message."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    start: Annotated[int, Field(ge=0)]
    end: Annotated[int, Field(gt=0)]

    @model_validator(mode="after")
    def _end_is_after_start(self) -> Self:
        if self.end <= self.start:
            raise ValueError("source span must end after it starts")
        return self


class Requirement(BaseModel):
    """One version of what the customer wants. Immutable by design."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )

    trip_type: TripType = "unknown"
    origin_city: Annotated[str | None, Field(max_length=_MAX_CITY_LENGTH)] = None
    destinations: Annotated[
        list[Annotated[str, Field(min_length=2, max_length=_MAX_CITY_LENGTH)]],
        Field(max_length=5),
    ] = Field(default_factory=list)
    start_date: date | None = None
    end_date: date | None = None
    date_flexibility_days: Annotated[int | None, Field(ge=0, le=60)] = None
    adults: Annotated[int | None, Field(ge=1, le=60)] = None
    children_ages: Annotated[
        list[Annotated[int, Field(ge=0, le=17)]],
        Field(max_length=8),
    ] = Field(default_factory=list)
    infants: Annotated[int, Field(ge=0, le=4)] = 0
    rooms: Annotated[int | None, Field(ge=1, le=30)] = None
    budget_paise: Annotated[int | None, Field(ge=0)] = None
    budget_currency: Literal["INR"] = "INR"
    preferences: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=_MAX_TAG_LENGTH)]],
        Field(max_length=_MAX_TAGS),
    ] = Field(default_factory=list)
    nationality: str | None = None
    special_needs: Annotated[
        list[Annotated[str, Field(min_length=1, max_length=_MAX_TAG_LENGTH)]],
        Field(max_length=_MAX_TAGS),
    ] = Field(default_factory=list)
    urgency: Urgency = "normal"

    @field_validator("origin_city", mode="before")
    @classmethod
    def _blank_city_is_unknown(cls, value: object) -> object:
        # A blank string carries no information. Turning it into "not stated"
        # lets the rules ask for the city; keeping "" would look like an answer.
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("nationality", mode="before")
    @classmethod
    def _blank_nationality_is_unknown(cls, value: object) -> object:
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("nationality")
    @classmethod
    def _normalise_country_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        code = value.upper()
        if len(code) != 2 or not code.isalpha():
            raise ValueError("nationality must be a two-letter ISO country code")
        return code

    @field_validator("budget_paise", mode="before")
    @classmethod
    def _money_is_integer_paise(cls, value: object) -> object:
        # Floats and strings are not money. The parser must emit integer paise;
        # a malformed value gets one repair rather than a silent rounding.
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError("budget_paise must be an integer number of paise")
        return value

    @model_validator(mode="after")
    def _dates_are_ordered(self) -> Self:
        if (
            self.start_date is not None
            and self.end_date is not None
            and self.end_date < self.start_date
        ):
            raise ValueError("end_date must not be before start_date")
        return self

    @property
    def budget(self) -> Money | None:
        """Budget as the only money type in the system, or ``None``."""

        if self.budget_paise is None:
            return None
        return Money(self.budget_paise, self.budget_currency)

    def asserted_fields(self) -> tuple[str, ...]:
        """Fields the customer actually stated, excluding neutral defaults."""

        return tuple(
            field.value
            for field in RequirementField
            if not _is_neutral(field, getattr(self, field.value))
        )

    def to_json(self) -> dict[str, object]:
        """Schema-shaped JSON for persistence, gold data and review screens."""

        return self.model_dump(mode="json")


def _is_neutral(field: RequirementField, value: object) -> bool:
    if field in _NEUTRAL_LISTS:
        return not value
    if field is RequirementField.TRIP_TYPE:
        return value == "unknown"
    if field is RequirementField.INFANTS:
        return value == 0
    if field is RequirementField.URGENCY:
        return value == "normal"
    if field is RequirementField.BUDGET_CURRENCY:
        return value == "INR"
    return value is None


class ParsedRequirement(BaseModel):
    """A requirement plus the evidence the parser used to produce it.

    ``confidence`` is a score per asserted field (0..1). ``spans`` are the
    character ranges in the source message that support each field. A field the
    parser filled without a span is a silent assumption and must be rejected
    before the requirement is saved.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    requirement: Requirement
    confidence: dict[str, Annotated[float, Field(ge=0.0, le=1.0)]] = Field(default_factory=dict)
    spans: dict[str, tuple[SourceSpan, ...]] = Field(default_factory=dict)
    ambiguous_fields: tuple[str, ...] = ()

    @field_validator("confidence")
    @classmethod
    def _confidence_uses_known_fields(cls, value: dict[str, float]) -> dict[str, float]:
        unknown = sorted(set(value) - set(REQUIREMENT_FIELDS))
        if unknown:
            raise ValueError(f"confidence for unknown fields: {unknown}")
        return value

    @field_validator("spans")
    @classmethod
    def _spans_use_known_fields(
        cls, value: dict[str, tuple[SourceSpan, ...]]
    ) -> dict[str, tuple[SourceSpan, ...]]:
        unknown = sorted(set(value) - set(REQUIREMENT_FIELDS))
        if unknown:
            raise ValueError(f"source spans for unknown fields: {unknown}")
        return value

    @field_validator("ambiguous_fields")
    @classmethod
    def _ambiguity_uses_known_fields(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(value) - set(REQUIREMENT_FIELDS))
        if unknown:
            raise ValueError(f"ambiguous unknown fields: {unknown}")
        # Preserve the caller's order but remove duplicates.
        return tuple(dict.fromkeys(value))

    def silent_assumptions(self) -> tuple[str, ...]:
        """Asserted fields with no source span. This must always be empty."""

        return tuple(
            field for field in self.requirement.asserted_fields() if not self.spans.get(field)
        )

    def unscored_fields(self) -> tuple[str, ...]:
        """Asserted fields without a confidence score (an eval quality signal)."""

        return tuple(
            field for field in self.requirement.asserted_fields() if field not in self.confidence
        )

    def span_problems(self, message: str) -> tuple[str, ...]:
        """Spans that fall outside the message they claim to quote."""

        problems: list[str] = []
        for field, spans in self.spans.items():
            for span in spans:
                if span.end > len(message):
                    problems.append(
                        f"{field}: span {span.start}:{span.end} exceeds message length {len(message)}"
                    )
        return tuple(problems)

    def check_no_silent_assumptions(self) -> None:
        offenders = self.silent_assumptions()
        if offenders:
            raise SilentAssumptionError(
                f"fields asserted without a source span: {', '.join(offenders)}"
            )
