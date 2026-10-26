"""Clarifying-question wording is fixed, complete and free of invented detail."""

from __future__ import annotations

import pytest

from travelos.inquiry.questions import (
    draft_body,
    question_codes,
    question_for,
    unknown_codes,
)
from travelos.inquiry.requirement import REQUIREMENT_FIELDS
from travelos.inquiry.rules import EXTRA_QUESTION_CODES, MissingField


def field(code: str, *, reason: str = "missing", severity: str = "required") -> MissingField:
    return MissingField(
        field=code,
        severity=severity,  # type: ignore[arg-type]
        reason=reason,  # type: ignore[arg-type]
        question_code=code,
    )


def test_every_gap_code_has_reviewed_wording() -> None:
    codes = set(REQUIREMENT_FIELDS) | set(EXTRA_QUESTION_CODES)

    assert unknown_codes(codes) == ()
    for code in codes:
        question = question_for(code)
        assert question.endswith("?")
        assert question[0].isupper()


def test_ambiguous_questions_ask_for_confirmation() -> None:
    question = question_for("adults", reason="ambiguous")

    assert question.startswith("Just to confirm")
    assert "how many adults" in question


def test_unknown_code_and_reason_are_rejected() -> None:
    with pytest.raises(ValueError, match="unknown question code"):
        question_for("price_paise")
    with pytest.raises(ValueError, match="unknown gap reason"):
        question_for("adults", reason="maybe")


def test_body_is_numbered_in_gap_order_and_deduplicates_codes() -> None:
    gaps = (
        field("origin_city"),
        field("start_date"),
        field("origin_city"),
    )

    body = draft_body(gaps)

    assert body.startswith("Namaste!")
    assert "1. Which city will you be travelling from?" in body
    assert "2. What date would you like to start?" in body
    assert body.count("Which city") == 1
    assert question_codes(gaps) == ("origin_city", "start_date")


def test_missing_and_recommended_gaps_share_the_same_wording_rules() -> None:
    gaps = (
        field("rooms"),
        field("budget_paise", severity="recommended"),
    )

    body = draft_body(gaps)

    assert "How many rooms do you need?" in body
    assert "Do you have a budget range in mind?" in body


def test_an_empty_gap_list_cannot_produce_a_message() -> None:
    with pytest.raises(ValueError, match="no gaps"):
        draft_body(())
