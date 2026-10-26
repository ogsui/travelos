"""Required-field policy: blocking gaps, ambiguity and optional questions."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from travelos.inquiry.requirement import REQUIREMENT_FIELDS, Requirement
from travelos.inquiry.rules import (
    EXTRA_QUESTION_CODES,
    MissingField,
    blocking_missing_fields,
    is_ready,
    missing_field_names,
    missing_fields,
    recommended_fields,
    required_fields,
)

TRIP_TYPES = ("flight", "package", "hotel", "visa", "corporate", "group", "unknown")


def complete(trip_type: str, **overrides: Any) -> Requirement:
    values: dict[str, Any] = {
        "trip_type": trip_type,
        "origin_city": "Delhi",
        "destinations": ["Goa"],
        "start_date": date(2026, 11, 2),
        "end_date": date(2026, 11, 7),
        "date_flexibility_days": 0,
        "adults": 2,
        "rooms": 2,
        "nationality": "IN",
    }
    values.update(overrides)
    return Requirement(**values)


def test_required_fields_per_trip_type() -> None:
    assert required_fields("flight") == ("origin_city", "destinations", "start_date", "adults")
    assert required_fields("package") == ("destinations", "start_date", "adults", "rooms")
    assert required_fields("hotel") == (
        "destinations",
        "start_date",
        "end_date",
        "adults",
        "rooms",
    )
    assert required_fields("visa") == ("destinations", "nationality", "start_date", "adults")
    assert required_fields("corporate") == ("origin_city", "destinations", "start_date", "adults")
    assert required_fields("group") == (
        "origin_city",
        "destinations",
        "start_date",
        "adults",
        "rooms",
    )
    assert required_fields("unknown") == ("trip_type",)
    assert required_fields("nonsense") == ("trip_type",)


def test_required_and_recommended_fields_are_known_and_disjoint() -> None:
    for trip_type in TRIP_TYPES:
        required = set(required_fields(trip_type))
        recommended = set(recommended_fields(trip_type))
        assert required <= set(REQUIREMENT_FIELDS) | {"trip_type"}
        assert recommended <= set(REQUIREMENT_FIELDS)
        assert required.isdisjoint(recommended)


def test_budget_is_always_recommended_and_never_blocking() -> None:
    requirement = complete("flight")

    assert is_ready(requirement)
    assert missing_field_names(requirement) == ("budget_paise",)
    assert missing_fields(requirement)[0].severity == "recommended"
    assert blocking_missing_fields(requirement) == ()


def test_unknown_trip_type_blocks_everything_until_it_is_asked() -> None:
    requirement = Requirement()

    gaps = missing_fields(requirement)

    assert gaps == (
        MissingField(
            field="trip_type", severity="required", reason="missing", question_code="trip_type"
        ),
    )
    assert not is_ready(requirement)


def test_unknown_trip_type_reports_ambiguity() -> None:
    gaps = missing_fields(Requirement(), ambiguous_fields=("trip_type",))

    assert gaps[0].reason == "ambiguous"
    assert gaps[0].blocking


def test_all_required_fields_are_reported_before_recommended_ones() -> None:
    gaps = missing_fields(Requirement(trip_type="hotel"))

    assert [item.field for item in gaps] == [
        "destinations",
        "start_date",
        "end_date",
        "adults",
        "rooms",
        "budget_paise",
    ]
    assert [item.blocking for item in gaps] == [True, True, True, True, True, False]


def test_hotel_needs_a_checkout_date() -> None:
    requirement = complete("hotel", end_date=None)

    assert missing_field_names(requirement) == ("end_date", "budget_paise")


def test_visa_needs_nationality_and_asks_about_documents() -> None:
    requirement = complete("visa", nationality=None)

    gaps = missing_fields(requirement)

    assert gaps[0].field == "nationality"
    assert gaps[0].severity == "required"
    assert gaps[-1].field == "traveller_documents"
    assert gaps[-1].severity == "recommended"


def test_infants_trigger_an_age_question() -> None:
    requirement = complete("package", infants=1)

    assert "infant_age" in missing_field_names(requirement)
    assert missing_fields(requirement)[-1].severity == "recommended"


def test_missing_flexibility_asks_whether_dates_are_confirmed() -> None:
    requirement = complete("flight", date_flexibility_days=None)

    assert missing_field_names(requirement) == ("budget_paise", "confirmed_dates")
    assert "confirmed_dates" not in missing_field_names(complete("flight", date_flexibility_days=0))


def test_confirmed_dates_is_not_asked_without_a_start_date() -> None:
    requirement = complete("flight", start_date=None, date_flexibility_days=None)

    assert "confirmed_dates" not in missing_field_names(requirement)
    assert "start_date" in missing_field_names(requirement)


def test_ambiguous_required_field_is_not_silently_accepted() -> None:
    requirement = complete("flight")

    gaps = missing_fields(requirement, ambiguous_fields=("destinations",))

    first = gaps[0]
    assert first.field == "destinations"
    assert first.reason == "ambiguous"
    assert first.blocking
    assert not is_ready(requirement, ambiguous_fields=("destinations",))


def test_ambiguity_outside_the_policy_is_surfaced_as_recommended() -> None:
    requirement = complete("flight", rooms=2)

    gaps = missing_fields(requirement, ambiguous_fields=("rooms",))

    assert gaps[-1] == MissingField(
        field="rooms", severity="recommended", reason="ambiguous", question_code="rooms"
    )


def test_unknown_ambiguous_code_is_a_programming_error() -> None:
    with pytest.raises(ValueError, match="price_paise"):
        missing_fields(Requirement(), ambiguous_fields=("price_paise",))


def test_missing_field_names_are_unique() -> None:
    requirement = complete("package", infants=2, date_flexibility_days=None)

    names = missing_field_names(requirement, ambiguous_fields=("rooms",))

    assert len(names) == len(set(names))


def test_every_extra_question_code_is_allowed() -> None:
    for code in EXTRA_QUESTION_CODES:
        assert MissingField(code, "recommended", "missing", code).field == code


def test_missing_field_rejects_unknown_codes_and_empty_question_codes() -> None:
    with pytest.raises(ValueError):
        MissingField("price_paise", "required", "missing", "price_paise")
    with pytest.raises(ValueError):
        MissingField("destinations", "required", "missing", "")


def test_missing_field_serialises_for_persistence() -> None:
    item = MissingField("adults", "required", "ambiguous", "adults")

    assert item.as_dict() == {
        "field": "adults",
        "severity": "required",
        "reason": "ambiguous",
        "question_code": "adults",
        "blocking": True,
    }
