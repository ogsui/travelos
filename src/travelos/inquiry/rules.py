"""Deterministic required-field policy per trip type (task 010a).

The parser extracts; this module decides what is still missing. It is plain code
on purpose: the question "can we search suppliers yet?" must be reviewable,
testable and identical on every run. No LLM is involved.

Policy (owner-editable by changing this file, with tests):

| trip type | required before searching | recommended, never blocking |
|---|---|---|
| flight | origin, destinations, start date, adults | end date, budget |
| package | destinations, start date, adults, rooms | origin, end date, budget |
| hotel | destinations, start date, end date, adults, rooms | budget |
| visa | destinations, nationality, start date, adults | traveller documents |
| corporate | origin, destinations, start date, adults | end date, budget |
| group | origin, destinations, start date, adults, rooms | end date, budget |
| unknown | the trip type itself, then re-run | - |

Budget is requested but optional by owner decision: quotes are built in tiers and
a missing budget must not block a human from quoting. ``date_flexibility_days``
is not a separate question: when a start date is known but flexibility is not, we
ask ``confirmed_dates``. ``infants > 0`` always asks ``infant_age`` because the
schema has no infant-age field.

An *ambiguous* field (the customer said two conflicting things) is reported even
when a value is present, so the parser can never quietly pick one reading.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Final, Literal

from travelos.inquiry.requirement import (
    REQUIREMENT_FIELDS,
    Requirement,
    RequirementField,
)

Severity = Literal["required", "recommended"]
Reason = Literal["missing", "ambiguous"]

EXTRA_QUESTION_CODES: Final[tuple[str, ...]] = (
    "infant_age",
    "confirmed_dates",
    "traveller_documents",
)
_KNOWN_CODES: Final[frozenset[str]] = frozenset(REQUIREMENT_FIELDS) | frozenset(
    EXTRA_QUESTION_CODES
)

_REQUIRED: Final[dict[str, tuple[RequirementField, ...]]] = {
    "flight": (
        RequirementField.ORIGIN_CITY,
        RequirementField.DESTINATIONS,
        RequirementField.START_DATE,
        RequirementField.ADULTS,
    ),
    "package": (
        RequirementField.DESTINATIONS,
        RequirementField.START_DATE,
        RequirementField.ADULTS,
        RequirementField.ROOMS,
    ),
    "hotel": (
        RequirementField.DESTINATIONS,
        RequirementField.START_DATE,
        RequirementField.END_DATE,
        RequirementField.ADULTS,
        RequirementField.ROOMS,
    ),
    "visa": (
        RequirementField.DESTINATIONS,
        RequirementField.NATIONALITY,
        RequirementField.START_DATE,
        RequirementField.ADULTS,
    ),
    "corporate": (
        RequirementField.ORIGIN_CITY,
        RequirementField.DESTINATIONS,
        RequirementField.START_DATE,
        RequirementField.ADULTS,
    ),
    "group": (
        RequirementField.ORIGIN_CITY,
        RequirementField.DESTINATIONS,
        RequirementField.START_DATE,
        RequirementField.ADULTS,
        RequirementField.ROOMS,
    ),
}

_RECOMMENDED: Final[dict[str, tuple[RequirementField, ...]]] = {
    "flight": (
        RequirementField.END_DATE,
        RequirementField.BUDGET_PAISE,
    ),
    "package": (
        RequirementField.ORIGIN_CITY,
        RequirementField.END_DATE,
        RequirementField.BUDGET_PAISE,
    ),
    "hotel": (RequirementField.BUDGET_PAISE,),
    "visa": (),
    "corporate": (
        RequirementField.END_DATE,
        RequirementField.BUDGET_PAISE,
    ),
    "group": (
        RequirementField.END_DATE,
        RequirementField.BUDGET_PAISE,
    ),
}

_LIST_FIELDS: Final[frozenset[str]] = frozenset(
    {
        RequirementField.DESTINATIONS.value,
        RequirementField.CHILDREN_AGES.value,
        RequirementField.PREFERENCES.value,
        RequirementField.SPECIAL_NEEDS.value,
    }
)


@dataclass(frozen=True, slots=True)
class MissingField:
    """One question to ask (or one field for a reviewer to resolve).

    ``field`` is a Requirement field name for schema values, or an extra question
    code (``infant_age``, ``confirmed_dates``, ``traveller_documents``) for
    intents the schema cannot store. ``question_code`` is what the drafting layer
    turns into wording; it equals ``field`` today.
    """

    field: str
    severity: Severity
    reason: Reason
    question_code: str

    def __post_init__(self) -> None:
        if self.field not in _KNOWN_CODES:
            raise ValueError(f"unknown missing-field code: {self.field!r}")
        if not self.question_code:
            raise ValueError("question_code must not be empty")

    @property
    def blocking(self) -> bool:
        return self.severity == "required"

    def as_dict(self) -> dict[str, str | bool]:
        return {
            "field": self.field,
            "severity": self.severity,
            "reason": self.reason,
            "question_code": self.question_code,
            "blocking": self.blocking,
        }


def required_fields(trip_type: str) -> tuple[str, ...]:
    """Fields that must be present before supplier search, in question order."""

    if trip_type not in _REQUIRED:
        # An unknown or unrecognised trip type has no safe required set: ask the
        # type first and recompute afterwards.
        return (RequirementField.TRIP_TYPE.value,)
    return tuple(field.value for field in _REQUIRED[trip_type])


def recommended_fields(trip_type: str) -> tuple[str, ...]:
    """Useful fields that improve a quote but never block it."""

    return tuple(field.value for field in _RECOMMENDED.get(trip_type, ()))


def missing_fields(
    requirement: Requirement, *, ambiguous_fields: Iterable[str] = ()
) -> tuple[MissingField, ...]:
    """All gaps, blocking ones first, with ambiguity explicit.

    ``ambiguous_fields`` comes from ``ParsedRequirement``: the parser saw two
    conflicting readings. Those fields are reported as ``reason="ambiguous"``
    even though the requirement holds a value.
    """

    ambiguous = _validated_ambiguity(ambiguous_fields)
    if requirement.trip_type == "unknown":
        reason: Reason = "ambiguous" if RequirementField.TRIP_TYPE.value in ambiguous else "missing"
        return (
            MissingField(
                field=RequirementField.TRIP_TYPE.value,
                severity="required",
                reason=reason,
                question_code=RequirementField.TRIP_TYPE.value,
            ),
        )

    blocking = _REQUIRED.get(requirement.trip_type, ())
    recommended = _RECOMMENDED.get(requirement.trip_type, ())
    blocking_names = {field.value for field in blocking}

    items: list[MissingField] = []
    seen: set[str] = set()

    for field in blocking:
        item = _gap(requirement, field, "required", ambiguous)
        if item is not None:
            items.append(item)
            seen.add(item.field)

    for field in recommended:
        item = _gap(requirement, field, "recommended", ambiguous)
        if item is not None:
            items.append(item)
            seen.add(item.field)

    for item in _conditional_questions(requirement):
        if item.field not in seen:
            items.append(item)
            seen.add(item.field)

    # An ambiguity outside the trip's policy (for example two different room
    # counts on a flight) still needs resolution; surface it, never discard it.
    for name in ambiguous:
        if name in seen:
            continue
        severity: Severity = "required" if name in blocking_names else "recommended"
        items.append(
            MissingField(field=name, severity=severity, reason="ambiguous", question_code=name)
        )
        seen.add(name)

    return tuple(items)


def blocking_missing_fields(
    requirement: Requirement, *, ambiguous_fields: Iterable[str] = ()
) -> tuple[MissingField, ...]:
    return tuple(
        item
        for item in missing_fields(requirement, ambiguous_fields=ambiguous_fields)
        if item.blocking
    )


def missing_field_names(
    requirement: Requirement, *, ambiguous_fields: Iterable[str] = ()
) -> tuple[str, ...]:
    """The ``missing_fields`` vocabulary saved on a requirement version."""

    return tuple(
        item.field for item in missing_fields(requirement, ambiguous_fields=ambiguous_fields)
    )


def is_ready(requirement: Requirement, *, ambiguous_fields: Iterable[str] = ()) -> bool:
    """True when nothing blocks supplier search."""

    return not blocking_missing_fields(requirement, ambiguous_fields=ambiguous_fields)


def _gap(
    requirement: Requirement,
    field: RequirementField,
    severity: Severity,
    ambiguous: frozenset[str],
) -> MissingField | None:
    name = field.value
    if name in ambiguous:
        return MissingField(field=name, severity=severity, reason="ambiguous", question_code=name)
    if not _is_present(requirement, name):
        return MissingField(field=name, severity=severity, reason="missing", question_code=name)
    return None


def _is_present(requirement: Requirement, field: str) -> bool:
    value = getattr(requirement, field)
    if field in _LIST_FIELDS:
        return bool(value)
    return value is not None


def _conditional_questions(requirement: Requirement) -> tuple[MissingField, ...]:
    items: list[MissingField] = []
    if requirement.infants > 0:
        items.append(_extra("infant_age"))
    if requirement.start_date is not None and requirement.date_flexibility_days is None:
        items.append(_extra("confirmed_dates"))
    if requirement.trip_type == "visa":
        items.append(_extra("traveller_documents"))
    return tuple(items)


def _extra(code: str) -> MissingField:
    return MissingField(field=code, severity="recommended", reason="missing", question_code=code)


def _validated_ambiguity(ambiguous_fields: Iterable[str]) -> frozenset[str]:
    ambiguous = frozenset(ambiguous_fields)
    unknown = sorted(ambiguous - _KNOWN_CODES)
    if unknown:
        raise ValueError(f"ambiguous unknown fields: {unknown}")
    return ambiguous
