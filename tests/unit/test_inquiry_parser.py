"""Inquiry parser: evidence, confidence and failure modes (task 010b)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from travelos.core.clock import ManualClock
from travelos.inquiry.errors import RequirementParseError, SilentAssumptionError
from travelos.inquiry.parser import InquiryParser
from travelos.llm.types import DataClass, LLMResult

MESSAGE = "Need a return flight from Mumbai to Goa on 2026-11-02 for 2 adults."
FIELDS = (
    "trip_type",
    "origin_city",
    "destinations",
    "start_date",
    "end_date",
    "date_flexibility_days",
    "adults",
    "children_ages",
    "infants",
    "rooms",
    "budget_paise",
    "budget_currency",
    "preferences",
    "nationality",
    "special_needs",
    "urgency",
)


class StubGateway:
    def __init__(self, payload: object | None, *, prompt_version: int = 1) -> None:
        self.payload = payload
        self.prompt_version = prompt_version
        self.calls: list[dict[str, Any]] = []

    def complete(self, db: object, **kwargs: Any) -> LLMResult:
        self.calls.append(kwargs)
        return LLMResult(
            call_id=None,
            text="",
            parsed=self.payload,
            provider="stub",
            model="stub-1",
            prompt_name=str(kwargs["prompt_name"]),
            prompt_version=self.prompt_version,
            data_class=DataClass.CUSTOMER,
            input_tokens=0,
            output_tokens=0,
            cost_paise=0,
            currency="INR",
            latency_ms=0,
            cached=False,
            repairs=0,
        )


def requirement(**overrides: Any) -> dict[str, Any]:
    values: dict[str, Any] = {
        "trip_type": "flight",
        "origin_city": "Mumbai",
        "destinations": ["Goa"],
        "start_date": "2026-11-02",
        "end_date": None,
        "date_flexibility_days": None,
        "adults": 2,
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
    values.update(overrides)
    return values


def output(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "requirement": requirement(),
        "confidence": {
            "trip_type": 0.9,
            "origin_city": 0.98,
            "destinations": 0.98,
            "start_date": 0.95,
            "adults": 0.99,
        },
        "sources": {
            "trip_type": ["return flight"],
            "origin_city": ["Mumbai"],
            "destinations": ["Goa"],
            "start_date": ["2026-11-02"],
            "adults": ["2 adults"],
        },
        "ambiguous_fields": [],
    }
    payload.update(overrides)
    return payload


def parser(payload: object | None, **kwargs: Any) -> InquiryParser:
    return InquiryParser(
        StubGateway(payload, **kwargs), clock=ManualClock(datetime(2026, 10, 26, tzinfo=UTC))
    )


def test_parse_builds_spans_from_verbatim_quotes() -> None:
    outcome = parser(output()).parse(None, message=MESSAGE, channel="whatsapp")

    assert MESSAGE[slice_(outcome.parsed.spans["origin_city"][0])] == "Mumbai"
    assert MESSAGE[slice_(outcome.parsed.spans["destinations"][0])] == "Goa"
    assert MESSAGE[slice_(outcome.parsed.spans["adults"][0])] == "2 adults"
    assert outcome.parsed.confidence["adults"] == 0.99
    assert outcome.prompt_name == "inquiry_parser"
    assert outcome.prompt_version == 1
    assert outcome.provider == "stub"
    assert outcome.model == "stub-1"


def slice_(span: Any) -> slice:
    return slice(span.start, span.end)


def test_parse_passes_code_owned_metadata_and_normalises_the_hint() -> None:
    gateway = StubGateway(output())
    inquiry_parser = InquiryParser(
        gateway,  # type: ignore[arg-type]
        clock=ManualClock(datetime(2026, 10, 26, tzinfo=UTC)),
    )

    inquiry_parser.parse(None, message=MESSAGE, channel=" whatsapp ", language_hint="HI")

    variables = gateway.calls[0]["variables"]
    assert variables["message"] == MESSAGE
    assert variables["channel"] == "whatsapp"
    assert variables["language_hint"] == "hi"
    assert variables["current_date"] == "2026-10-26"
    assert gateway.calls[0]["data_class"] is DataClass.CUSTOMER
    assert gateway.calls[0]["prompt_name"] == "inquiry_parser"


def test_unexpected_language_hint_becomes_unknown() -> None:
    gateway = StubGateway(output())
    InquiryParser(gateway).parse(  # type: ignore[arg-type]
        None, message=MESSAGE, channel="email", language_hint="klingon"
    )

    assert gateway.calls[0]["variables"]["language_hint"] == "unknown"


def test_empty_message_and_channel_are_rejected_without_a_call() -> None:
    gateway = StubGateway(output())
    inquiry_parser = InquiryParser(gateway)  # type: ignore[arg-type]

    with pytest.raises(RequirementParseError, match="empty message"):
        inquiry_parser.parse(None, message="   ", channel="email")
    with pytest.raises(RequirementParseError, match="channel"):
        inquiry_parser.parse(None, message=MESSAGE, channel=" ")
    assert gateway.calls == []


def test_asserted_field_without_a_source_is_a_silent_assumption() -> None:
    payload = output()
    payload["sources"].pop("adults")
    payload["confidence"].pop("adults")

    with pytest.raises(SilentAssumptionError, match="adults"):
        parser(payload).parse(None, message=MESSAGE, channel="email")


def test_evidence_without_confidence_is_rejected() -> None:
    payload = output()
    payload["confidence"].pop("adults")

    with pytest.raises(RequirementParseError, match="without confidence"):
        parser(payload).parse(None, message=MESSAGE, channel="email")


def test_quote_that_is_not_in_the_message_is_rejected() -> None:
    payload = output()
    payload["sources"]["adults"] = ["3 adults"]

    with pytest.raises(RequirementParseError, match="does not occur"):
        parser(payload).parse(None, message=MESSAGE, channel="email")


def test_source_for_a_null_field_is_rejected() -> None:
    payload = output()
    payload["sources"]["rooms"] = ["Goa"]
    payload["confidence"]["rooms"] = 0.9

    with pytest.raises(RequirementParseError, match="without a value"):
        parser(payload).parse(None, message=MESSAGE, channel="email")


def test_unexpected_top_level_output_is_rejected() -> None:
    payload = output(action="book")

    with pytest.raises(RequirementParseError, match="unexpected parser output"):
        parser(payload).parse(None, message=MESSAGE, channel="email")


def test_invalid_requirement_values_are_rejected() -> None:
    payload = output(requirement=requirement(adults=0))

    with pytest.raises(RequirementParseError, match="failed validation"):
        parser(payload).parse(None, message=MESSAGE, channel="email")


def test_no_structured_output_is_rejected() -> None:
    with pytest.raises(RequirementParseError, match="no structured output"):
        parser(None).parse(None, message=MESSAGE, channel="email")


def test_unknown_evidence_keys_are_rejected() -> None:
    payload = output()
    payload["confidence"]["price_paise"] = 0.5

    with pytest.raises(RequirementParseError, match="failed validation"):
        parser(payload).parse(None, message=MESSAGE, channel="email")


def test_quotes_tolerate_case_and_whitespace_differences() -> None:
    message = "Need a return   flight from mumbai to GOA on 2026-11-02 for 2 adults."
    payload = output()
    payload["sources"]["trip_type"] = ["return flight"]
    payload["sources"]["origin_city"] = ["Mumbai"]
    payload["sources"]["destinations"] = ["Goa"]

    outcome = parser(payload).parse(None, message=message, channel="email")

    assert message[slice_(outcome.parsed.spans["trip_type"][0])] == "return   flight"
    assert message[slice_(outcome.parsed.spans["origin_city"][0])] == "mumbai"
    assert message[slice_(outcome.parsed.spans["destinations"][0])] == "GOA"


def test_ambiguous_fields_are_forwarded() -> None:
    payload = output(ambiguous_fields=["adults"])

    outcome = parser(payload).parse(None, message=MESSAGE, channel="email")

    assert outcome.parsed.ambiguous_fields == ("adults",)


def test_a_neutral_default_may_have_explicit_evidence() -> None:
    message = "2 adults, no rush"
    payload = output(
        requirement=requirement(
            trip_type="unknown",
            origin_city=None,
            destinations=[],
            start_date=None,
            urgency="normal",
        ),
        sources={
            "adults": ["2 adults"],
            "urgency": ["no rush"],
        },
        confidence={"adults": 0.99, "urgency": 0.9},
    )

    outcome = parser(payload).parse(None, message=message, channel="email")

    assert outcome.parsed.requirement.urgency == "normal"
    assert message[slice_(outcome.parsed.spans["urgency"][0])] == "no rush"


def test_empty_quotes_and_bad_shapes_are_rejected() -> None:
    payload = output()
    payload["sources"]["adults"] = []

    with pytest.raises(RequirementParseError, match="at least one quote"):
        parser(payload).parse(None, message=MESSAGE, channel="email")

    payload = output()
    payload["ambiguous_fields"] = "adults"

    with pytest.raises(RequirementParseError, match="must be a list"):
        parser(payload).parse(None, message=MESSAGE, channel="email")
