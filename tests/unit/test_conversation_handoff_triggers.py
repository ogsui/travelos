"""Deterministic handoff triggers (task 011b)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from travelos.conversation.triggers import (
    HandoffPolicy,
    HandoffTrigger,
    detect_amount_triggers,
    detect_keyword_triggers,
    detect_loop_triggers,
    detect_triggers,
)

NOW = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)


def kinds(texts: list[str]) -> list[HandoffTrigger]:
    return [evidence.trigger for evidence in detect_keyword_triggers(texts)]


def test_anger_and_complaint_language_is_detected() -> None:
    assert kinds(["I am furious. I want to complain to your manager."]) == [
        HandoffTrigger.ANGER,
        HandoffTrigger.COMPLAINT,
    ]


def test_legal_visa_and_refund_language_is_detected() -> None:
    assert HandoffTrigger.LEGAL in kinds(["We will take legal action through our lawyer."])
    assert HandoffTrigger.VISA in kinds(["Do I need a visa for Thailand?"])
    assert HandoffTrigger.REFUND in kinds(["I want a refund and to cancel my booking."])


def test_plain_holiday_requests_do_not_trigger_keywords() -> None:
    assert kinds(["Two adults, three nights in Goa in February."]) == []
    assert kinds(["Please share some options for a family holiday."]) == []


def test_keyword_matching_is_case_insensitive_and_whole_word() -> None:
    assert kinds(["FURIOUS about this"]) == [HandoffTrigger.ANGER]
    # "madam" must not be read as "mad"; "class" must not be read as "ass".
    assert kinds(["Madam, please share the class of hotel."]) == []


def test_hinglish_terms_are_detected() -> None:
    found = kinds(["Yeh service bahut bakwas hai, main shikayat karunga."])
    assert HandoffTrigger.ANGER in found
    assert HandoffTrigger.COMPLAINT in found


@pytest.mark.parametrize(
    "text",
    [
        "Our budget is ₹45,000.",
        "Rs. 45000 for the package",
        "45000 rupees total",
        "Around 1.5 lakh",
        "INR 75,000",
        "Budget is 2 crore",
    ],
)
def test_amounts_are_detected_in_common_rupee_forms(text: str) -> None:
    evidence = detect_amount_triggers([text])
    assert [item.trigger for item in evidence] == [HandoffTrigger.BOOKING_VALUE]
    assert "amount mentioned" in evidence[0].detail


def test_non_money_numbers_do_not_trigger() -> None:
    assert (
        detect_amount_triggers(["2 adults, 3 nights, flight AI 805 on 2027-02-01 and 2 rooms."])
        == ()
    )


def test_booking_value_and_stated_budget_trigger_without_any_text() -> None:
    assert detect_amount_triggers([], booking_value_paise=100)[0].trigger == (
        HandoffTrigger.BOOKING_VALUE
    )
    assert detect_amount_triggers([], stated_budget_paise=15_000_000)[0].trigger == (
        HandoffTrigger.BOOKING_VALUE
    )
    assert detect_amount_triggers([], booking_value_paise=0) == ()
    assert detect_amount_triggers([], stated_budget_paise=0) == ()


def test_the_value_floor_is_configurable() -> None:
    assert (
        detect_amount_triggers([], booking_value_paise=5_000_000, min_value_paise=10_000_000) == ()
    )
    assert (
        detect_amount_triggers([], booking_value_paise=10_000_000, min_value_paise=10_000_000)[
            0
        ].trigger
        == HandoffTrigger.BOOKING_VALUE
    )


@pytest.mark.parametrize("value", [-1, True])
def test_invalid_amounts_are_rejected(value: int) -> None:
    with pytest.raises(ValueError):
        detect_amount_triggers([], booking_value_paise=value)


def test_a_repeated_question_is_a_loop() -> None:
    messages = [
        (NOW - timedelta(hours=3), "Do you have a visa for Thailand?"),
        (NOW - timedelta(hours=2), "Do you have a visa for Thailand?"),
        (NOW - timedelta(hours=1), "Do you have a visa for Thailand?"),
    ]
    evidence = detect_loop_triggers(messages, now=NOW)
    assert [item.trigger for item in evidence] == [HandoffTrigger.LOOP]
    assert "3 times" in evidence[0].detail


def test_non_questions_and_two_repeats_are_not_a_loop() -> None:
    two = [
        (NOW - timedelta(hours=2), "Do you have a visa for Thailand?"),
        (NOW - timedelta(hours=1), "Do you have a visa for Thailand?"),
    ]
    assert detect_loop_triggers(two, now=NOW) == ()
    greetings = [(NOW - timedelta(minutes=i), "Hi") for i in range(5)]
    assert detect_loop_triggers(greetings, now=NOW) == ()


def test_messages_outside_the_loop_window_are_ignored() -> None:
    messages = [
        (NOW - timedelta(days=10), "Do you have a visa for Thailand?"),
        (NOW - timedelta(days=9), "Do you have a visa for Thailand?"),
        (NOW - timedelta(days=8), "Do you have a visa for Thailand?"),
    ]
    assert detect_loop_triggers(messages, now=NOW) == ()


def test_detect_triggers_orders_deduplicates_and_combines_sources() -> None:
    messages = [
        (NOW - timedelta(hours=3), "Do you have a visa for Thailand?"),
        (NOW - timedelta(hours=2), "Do you have a visa for Thailand?"),
        (NOW - timedelta(hours=1), "Do you have a visa for Thailand?"),
    ]
    evidence = detect_triggers(
        texts=["I want a refund, I will complain, budget ₹45,000"],
        loop_messages=messages,
        now=NOW,
    )
    assert [item.trigger for item in evidence] == [
        HandoffTrigger.COMPLAINT,
        HandoffTrigger.BOOKING_VALUE,
        HandoffTrigger.REFUND,
        HandoffTrigger.LOOP,
    ]


def test_policy_decides_priority_and_assignee() -> None:
    policy = HandoffPolicy()
    assert policy.priority([HandoffTrigger.COMPLAINT]) == "high"
    assert policy.assignee_role([HandoffTrigger.COMPLAINT]) == "agent"
    assert policy.priority([HandoffTrigger.BOOKING_VALUE]) == "normal"
    assert policy.assignee_role([HandoffTrigger.BOOKING_VALUE]) == "owner"
    assert policy.priority([HandoffTrigger.LEGAL]) == "high"
    assert policy.assignee_role([HandoffTrigger.REFUND]) == "owner"


def test_invalid_policy_is_rejected() -> None:
    with pytest.raises(ValueError):
        HandoffPolicy(min_value_paise=-1)
    with pytest.raises(ValueError):
        HandoffPolicy(loop_repeat_threshold=2)
    with pytest.raises(ValueError):
        HandoffPolicy(loop_window=timedelta(0))
