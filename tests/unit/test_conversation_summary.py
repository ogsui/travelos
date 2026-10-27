"""Deterministic conversation summaries for handoffs (task 011b)."""

from __future__ import annotations

from datetime import UTC, date, datetime

from travelos.conversation.summary import ConversationFacts, build_summary
from travelos.conversation.triggers import HandoffTrigger, TriggerEvidence
from travelos.inquiry.requirement import Requirement

NOW = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)


def facts(**overrides: object) -> ConversationFacts:
    values: dict[str, object] = {
        "inquiry_state": "RECEIVED",
        "trip_type": "package",
        "urgency": "normal",
        "customer_name": "Synthetic Family",
        "channel": "whatsapp",
        "language": "en",
        "requirement": Requirement(
            trip_type="package",
            origin_city="Mumbai",
            destinations=["Goa"],
            start_date=date(2027, 2, 1),
            end_date=date(2027, 2, 5),
            adults=2,
            children_ages=[4],
            infants=1,
            rooms=2,
            budget_paise=15_000_000,
            preferences=["beach"],
            special_needs=["wheelchair access"],
        ),
        "stated_fields": ("destinations", "start_date", "budget_paise"),
        "missing_fields": ("end_date (recommended)",),
        "inbound_count": 4,
        "outbound_count": 2,
        "last_inbound_at": NOW,
        "last_outbound_at": NOW,
        "last_inbound": "I want to complain about the hotel.",
        "last_outbound": "Thank you, we are checking.",
        "pending_drafts": 1,
        "generated_at": NOW,
    }
    values.update(overrides)
    return ConversationFacts(**values)  # type: ignore[arg-type]


def test_summary_contains_the_facts_staff_need() -> None:
    summary = build_summary(
        facts(),
        (
            TriggerEvidence(HandoffTrigger.COMPLAINT, "matched 'complain'"),
            TriggerEvidence(HandoffTrigger.BOOKING_VALUE, "amount mentioned '₹45,000'"),
        ),
    )

    assert summary.startswith("Conversation handoff summary (v1)")
    assert "Inquiry: RECEIVED · package · urgency normal" in summary
    assert "Customer: Synthetic Family" in summary
    assert "Channel: whatsapp · language en" in summary
    assert "2 adults, 1 child (age 4), 1 infant, 2 rooms" in summary
    assert "from Mumbai to Goa" in summary
    assert "2027-02-01 to 2027-02-05" in summary
    assert "budget ₹150,000.00" in summary
    assert "Stated: destinations, start_date, budget_paise" in summary
    assert "Missing: end_date (recommended)" in summary
    assert "Preferences: beach" in summary
    assert "Special needs: wheelchair access" in summary
    assert "Messages: 4 inbound, 2 outbound" in summary
    assert 'Last customer message: "I want to complain about the hotel."' in summary
    assert 'Last outbound message: "Thank you, we are checking."' in summary
    assert "Drafts waiting for approval: 1" in summary
    assert "Triggers:" in summary
    assert "complaint: matched 'complain'" in summary
    assert "booking value: amount mentioned '₹45,000'" in summary
    # The trigger list comes first so truncation cannot hide why this exists.
    assert summary.index("Triggers:") < summary.index("Inquiry:")


def test_summary_says_when_nothing_is_on_record() -> None:
    summary = build_summary(
        facts(
            customer_name=None,
            requirement=None,
            stated_fields=(),
            missing_fields=(),
            inbound_count=0,
            outbound_count=0,
            last_inbound_at=None,
            last_outbound_at=None,
            last_inbound=None,
            last_outbound=None,
            pending_drafts=0,
        ),
        (),
    )

    assert "Customer: not linked" in summary
    assert "Requirement: not parsed yet" in summary
    assert "Last customer message: none on record" in summary
    assert "Last outbound message: none on record" in summary
    assert "Drafts waiting for approval: 0" in summary


def test_summary_truncates_long_details() -> None:
    summary = build_summary(
        facts(missing_fields=tuple("x" * 100 for _ in range(100))),
        (TriggerEvidence(HandoffTrigger.ANGER, "matched 'furious'"),),
    )

    assert len(summary) <= 4000
    assert summary.endswith("[summary truncated]")


def test_summary_caps_a_long_message_excerpt() -> None:
    summary = build_summary(
        facts(last_inbound="x" * 5000),
        (),
    )

    assert "x" * 280 not in summary
    assert '"' + "x" * 277 + '..."' in summary


def test_summary_uses_singular_and_plural_traveller_labels() -> None:
    requirement = Requirement(
        trip_type="flight",
        destinations=["Delhi"],
        adults=1,
        children_ages=[7, 9],
        rooms=1,
        budget_paise=None,
    )
    summary = build_summary(facts(requirement=requirement), ())
    assert "1 adult, 2 children (ages 7, 9), 1 room" in summary
    assert "budget not stated" in summary
