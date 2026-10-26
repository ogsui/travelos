"""Requirement model: schema parity, neutral defaults, evidence and immutability."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from travelos.core.money import Money
from travelos.inquiry.errors import SilentAssumptionError
from travelos.inquiry.requirement import (
    REQUIREMENT_FIELDS,
    ParsedRequirement,
    Requirement,
    SourceSpan,
)
from travelos.llm.schema import validate_json

REPO_ROOT = Path(__file__).resolve().parents[2]
INQUIRY_SCHEMA: dict[str, Any] = json.loads(
    (REPO_ROOT / "data" / "schemas" / "inquiry.schema.json").read_text(encoding="utf-8")
)
REQUIREMENT_SCHEMA: dict[str, Any] = INQUIRY_SCHEMA["$defs"]["requirement"]


def test_field_names_match_the_committed_json_schema() -> None:
    assert set(Requirement.model_fields) == set(REQUIREMENT_SCHEMA["properties"])
    assert set(Requirement.model_fields) == set(REQUIREMENT_SCHEMA["required"])


def test_defaults_are_neutral_and_schema_valid() -> None:
    requirement = Requirement()

    assert requirement.asserted_fields() == ()
    assert requirement.to_json() == {
        "trip_type": "unknown",
        "origin_city": None,
        "destinations": [],
        "start_date": None,
        "end_date": None,
        "date_flexibility_days": None,
        "adults": None,
        "children_ages": [],
        "infants": 0,
        "rooms": None,
        "budget_paise": None,
        "budget_currency": "INR",
        "preferences": [],
        "nationality": None,
        "special_needs": [],
        "urgency": "normal",
    }
    assert validate_json(requirement.to_json(), REQUIREMENT_SCHEMA) == []


def test_full_requirement_round_trips_and_validates() -> None:
    requirement = Requirement(
        trip_type="package",
        origin_city="Mumbai",
        destinations=["Goa"],
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 7),
        date_flexibility_days=1,
        adults=2,
        children_ages=[5, 9],
        infants=1,
        rooms=2,
        budget_paise=8_650_000,
        preferences=["kid_friendly", "vegetarian_meals"],
        nationality="in",
        special_needs=["connecting_rooms"],
        urgency="high",
    )

    payload = requirement.to_json()

    assert payload["start_date"] == "2026-11-02"
    assert payload["nationality"] == "IN"
    assert validate_json(payload, REQUIREMENT_SCHEMA) == []
    assert Requirement.model_validate(payload) == requirement


def test_unknown_fields_are_rejected() -> None:
    with pytest.raises(ValidationError):
        Requirement.model_validate({"trip_type": "flight", "price_paise": 100})


def test_end_date_before_start_date_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Requirement(start_date=date(2026, 11, 7), end_date=date(2026, 11, 2))


def test_partial_dates_are_allowed() -> None:
    assert Requirement(start_date=date(2026, 11, 2)).end_date is None
    assert Requirement(end_date=date(2026, 11, 2)).start_date is None


@pytest.mark.parametrize("budget", [8650000.0, "8650000", True])
def test_budget_must_be_integer_paise(budget: object) -> None:
    with pytest.raises(ValidationError):
        Requirement(budget_paise=budget)  # type: ignore[arg-type]


def test_budget_is_exposed_as_money() -> None:
    requirement = Requirement(budget_paise=8_650_000)

    assert requirement.budget == Money(8_650_000, "INR")
    assert Requirement().budget is None


@pytest.mark.parametrize("nationality", ["IND", "I", "1N", "  "])
def test_nationality_must_be_a_two_letter_code(nationality: str) -> None:
    if not nationality.strip():
        assert Requirement(nationality=nationality).nationality is None
        return
    with pytest.raises(ValidationError):
        Requirement(nationality=nationality)


def test_blank_origin_city_means_not_stated() -> None:
    assert Requirement(origin_city="   ").origin_city is None


def test_destination_must_be_a_real_name() -> None:
    with pytest.raises(ValidationError):
        Requirement(destinations=["G"])


def test_passenger_counts_are_bounded() -> None:
    for field, value in (("adults", 0), ("adults", 61), ("rooms", 0), ("infants", 5)):
        with pytest.raises(ValidationError):
            Requirement.model_validate({field: value})
    with pytest.raises(ValidationError):
        Requirement(children_ages=[18])
    with pytest.raises(ValidationError):
        Requirement(children_ages=[-1])


def test_requirement_is_immutable() -> None:
    requirement = Requirement(adults=2)

    with pytest.raises(ValidationError):
        requirement.adults = 3  # type: ignore[misc]


def test_asserted_fields_exclude_neutral_defaults() -> None:
    neutral = Requirement(urgency="normal", infants=0, budget_currency="INR", destinations=[])

    assert neutral.asserted_fields() == ()
    assert Requirement(adults=2, infants=1, urgency="high").asserted_fields() == (
        "adults",
        "infants",
        "urgency",
    )


def test_parsed_requirement_flags_asserted_fields_without_spans() -> None:
    parsed = ParsedRequirement(requirement=Requirement(adults=2, destinations=["Goa"]))

    assert parsed.silent_assumptions() == ("destinations", "adults")
    with pytest.raises(SilentAssumptionError, match="destinations"):
        parsed.check_no_silent_assumptions()


def test_span_backed_fields_pass_the_silent_assumption_check() -> None:
    parsed = ParsedRequirement(
        requirement=Requirement(adults=2),
        confidence={"adults": 0.92},
        spans={"adults": (SourceSpan(start=3, end=10),)},
    )

    assert parsed.silent_assumptions() == ()
    parsed.check_no_silent_assumptions()


def test_neutral_defaults_need_no_evidence() -> None:
    assert ParsedRequirement(requirement=Requirement()).silent_assumptions() == ()


def test_confidence_and_spans_reject_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        ParsedRequirement(requirement=Requirement(), confidence={"price_paise": 0.5})
    with pytest.raises(ValidationError):
        ParsedRequirement(
            requirement=Requirement(),
            spans={"price_paise": (SourceSpan(start=0, end=2),)},
        )


def test_confidence_must_be_a_probability() -> None:
    with pytest.raises(ValidationError):
        ParsedRequirement(requirement=Requirement(), confidence={"adults": 1.5})
    with pytest.raises(ValidationError):
        ParsedRequirement(requirement=Requirement(), confidence={"adults": -0.1})


def test_ambiguous_fields_are_known_and_deduplicated() -> None:
    parsed = ParsedRequirement(
        requirement=Requirement(), ambiguous_fields=("adults", "adults", "rooms")
    )

    assert parsed.ambiguous_fields == ("adults", "rooms")

    with pytest.raises(ValidationError):
        ParsedRequirement(requirement=Requirement(), ambiguous_fields=("price",))


def test_source_span_must_be_a_forward_range() -> None:
    assert SourceSpan(start=0, end=1).end == 1
    with pytest.raises(ValidationError):
        SourceSpan(start=4, end=4)
    with pytest.raises(ValidationError):
        SourceSpan(start=5, end=4)
    with pytest.raises(ValidationError):
        SourceSpan(start=-1, end=2)


def test_span_bounds_are_checked_against_the_message() -> None:
    parsed = ParsedRequirement(
        requirement=Requirement(adults=2),
        spans={"adults": (SourceSpan(start=0, end=10),)},
    )

    assert parsed.span_problems("2 adults") == ("adults: span 0:10 exceeds message length 8",)
    assert parsed.span_problems("2 adults, going to Goa") == ()


def test_unscored_fields_are_reported_separately() -> None:
    parsed = ParsedRequirement(
        requirement=Requirement(adults=2),
        spans={"adults": (SourceSpan(start=0, end=1),)},
    )

    assert parsed.unscored_fields() == ("adults",)


def test_requirement_fields_constant_matches_the_model() -> None:
    assert set(REQUIREMENT_FIELDS) == set(Requirement.model_fields)


CITIES = st.sampled_from(["Delhi", "Goa", "Mumbai", "Kochi", "Jaipur"])
DATES = st.dates(min_value=date(2026, 1, 1), max_value=date(2027, 12, 31))
TAGS = st.lists(st.sampled_from(["kid_friendly", "vegetarian_meals", "window_seat"]), max_size=3)


@st.composite
def requirements(draw: st.DrawFn) -> Requirement:
    start = draw(st.none() | DATES)
    end = draw(st.none() | DATES)
    if start is not None and end is not None and end < start:
        end = start
    return Requirement(
        trip_type=draw(
            st.sampled_from(["flight", "package", "hotel", "visa", "corporate", "group", "unknown"])
        ),
        origin_city=draw(st.none() | CITIES),
        destinations=draw(st.lists(CITIES, max_size=4)),
        start_date=start,
        end_date=end,
        date_flexibility_days=draw(st.none() | st.integers(min_value=0, max_value=60)),
        adults=draw(st.none() | st.integers(min_value=1, max_value=20)),
        children_ages=draw(st.lists(st.integers(min_value=0, max_value=17), max_size=4)),
        infants=draw(st.integers(min_value=0, max_value=3)),
        rooms=draw(st.none() | st.integers(min_value=1, max_value=6)),
        budget_paise=draw(st.none() | st.integers(min_value=0, max_value=1_000_000_000)),
        preferences=draw(TAGS),
        nationality=draw(st.none() | st.sampled_from(["IN", "US", "GB", "AE"])),
        special_needs=draw(TAGS),
        urgency=draw(st.sampled_from(["low", "normal", "high"])),
    )


@given(requirements())
def test_every_requirement_round_trips_and_validates_against_the_schema(
    requirement: Requirement,
) -> None:
    payload = requirement.to_json()

    assert validate_json(payload, REQUIREMENT_SCHEMA) == []
    assert Requirement.model_validate(payload) == requirement
