"""Inquiry persistence, state transitions, duplicates and draft gating (task 010c)."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from travelos.approval.models import ApprovalRequest
from travelos.channels.models import Conversation, Message
from travelos.core.audit import AuditWriter
from travelos.core.errors import AppendOnlyError
from travelos.core.models import HumanTask, User
from travelos.crm.fingerprints import ContactFingerprinter
from travelos.crm.models import Contact, Customer
from travelos.inquiry.errors import (
    InquiryNeedsInfoError,
    InquiryNoQuestionsError,
    InquiryNoRequirementError,
    InquiryNotFoundError,
    InquiryStateError,
    InquiryValidationError,
    SilentAssumptionError,
)
from travelos.inquiry.models import MessageDraft
from travelos.inquiry.parser import ParseOutcome
from travelos.inquiry.requirement import ParsedRequirement, Requirement, SourceSpan
from travelos.inquiry.service import InquiryService, match_customer_by_contact

pytestmark = pytest.mark.integration

MESSAGE = "We want a holiday package to Goa on 2026-11-02 for 2 adults."
MESSAGE_WITH_ROOMS = "We want a holiday package to Goa on 2026-11-02 for 2 adults in 2 rooms."
FLIGHT_MESSAGE = "Need a flight from Mumbai to Goa on 2026-11-02 for 2 adults."
PACKAGE_SOURCES = {
    "trip_type": ["holiday package"],
    "destinations": ["Goa"],
    "start_date": ["2026-11-02"],
    "adults": ["2 adults"],
}
FLIGHT_SOURCES = {
    "trip_type": ["flight"],
    "origin_city": ["Mumbai"],
    "destinations": ["Goa"],
    "start_date": ["2026-11-02"],
    "adults": ["2 adults"],
}
COMPLETE_FLIGHT_MESSAGE = (
    "Need a flight from Mumbai to Goa on 2026-11-02 returning 2026-11-09 "
    "for 2 adults, budget Rs 50000, 0 days flexible."
)
COMPLETE_FLIGHT_SOURCES = {
    "trip_type": ["flight"],
    "origin_city": ["Mumbai"],
    "destinations": ["Goa"],
    "start_date": ["2026-11-02"],
    "end_date": ["2026-11-09"],
    "date_flexibility_days": ["0 days"],
    "adults": ["2 adults"],
    "budget_paise": ["Rs 50000"],
}


def add_customer(db: Session, name: str = "Synthetic Family") -> Customer:
    customer = Customer(customer_type="family", display_name=name)
    db.add(customer)
    db.flush()
    return customer


def add_contact(
    db: Session,
    customer: Customer,
    *,
    value: str = "+919000000001",
    channel: str = "whatsapp",
    language: str | None = "en",
) -> Contact:
    contact = Contact(
        customer_id=customer.id,
        channel=channel,
        value=value,
        value_hash=ContactFingerprinter().fingerprint(channel, value),
        is_primary=True,
        language=language,
    )
    db.add(contact)
    db.flush()
    return contact


def add_conversation(
    db: Session,
    *,
    customer: Customer | None = None,
    contact: Contact | None = None,
    channel: str = "whatsapp",
    thread: str | None = None,
) -> Conversation:
    conversation = Conversation(
        customer_id=customer.id if customer is not None else None,
        contact_id=contact.id if contact is not None else None,
        channel=channel,
        external_thread_id=thread or f"thread-{uuid4()}",
    )
    db.add(conversation)
    db.flush()
    return conversation


def add_message(
    db: Session,
    conversation: Conversation,
    body: str = MESSAGE,
    *,
    direction: str = "inbound",
    occurred_at: datetime | None = None,
) -> Message:
    message = Message(
        conversation_id=conversation.id,
        direction=direction,
        channel=conversation.channel,
        body=body,
        external_id=f"msg-{uuid4()}",
        occurred_at=occurred_at or datetime(2026, 10, 26, 12, 0, tzinfo=UTC),
    )
    db.add(message)
    db.flush()
    return message


def add_user(db: Session, *, email: str = "agent@example.test") -> User:
    user = User(email=email, password_hash="x" * 20, role="agent")
    db.add(user)
    db.flush()
    return user


def parsed_outcome(
    requirement: Requirement,
    *,
    message: str,
    sources: dict[str, list[str]],
    confidence: dict[str, float] | None = None,
    ambiguous: tuple[str, ...] = (),
) -> ParseOutcome:
    spans: dict[str, tuple[SourceSpan, ...]] = {}
    for field, quotes in sources.items():
        spans[field] = tuple(
            SourceSpan(
                start=message.index(quote),
                end=message.index(quote) + len(quote),
            )
            for quote in quotes
        )
    return ParseOutcome(
        parsed=ParsedRequirement(
            requirement=requirement,
            confidence=confidence or {field: 0.9 for field in sources},
            spans=spans,
            ambiguous_fields=ambiguous,
        ),
        prompt_name="inquiry_parser",
        prompt_version=1,
        provider="fake",
        model="fake-1",
        call_id=uuid4(),
    )


def package_requirement(*, rooms: int | None = None) -> Requirement:
    return Requirement(
        trip_type="package",
        destinations=["Goa"],
        start_date=date(2026, 11, 2),
        adults=2,
        rooms=rooms,
    )


def flight_requirement() -> Requirement:
    return Requirement(
        trip_type="flight",
        origin_city="Mumbai",
        destinations=["Goa"],
        start_date=date(2026, 11, 2),
        adults=2,
    )


def complete_flight_requirement() -> Requirement:
    return Requirement(
        trip_type="flight",
        origin_city="Mumbai",
        destinations=["Goa"],
        start_date=date(2026, 11, 2),
        end_date=date(2026, 11, 9),
        date_flexibility_days=0,
        adults=2,
        budget_paise=5_000_000,
    )


def service() -> InquiryService:
    return InquiryService(audit=AuditWriter())


def audit_actions(db: Session) -> list[str]:
    db.flush()
    return [
        row[0] for row in db.execute(text("SELECT action FROM audit_log ORDER BY at, id")).all()
    ]


# --------------------------------------------------------------------------
# Creation and reads
# --------------------------------------------------------------------------


def test_create_uses_the_conversation_customer_and_audits(db: Session) -> None:
    customer = add_customer(db)
    contact = add_contact(db, customer)
    conversation = add_conversation(db, customer=customer, contact=contact)
    actor = add_user(db)

    inquiry = service().create(db, conversation_id=conversation.id, actor_id=actor.id)

    assert inquiry.state == "RECEIVED"
    assert inquiry.trip_type == "unknown"
    assert inquiry.customer_id == customer.id
    assert inquiry.conversation_id == conversation.id
    assert "inquiry.created" in audit_actions(db)
    assert service().get(db, inquiry.id).id == inquiry.id


def test_create_rejects_unknown_conversation_and_customer_mismatch(db: Session) -> None:
    customer = add_customer(db)
    conversation = add_conversation(db, customer=customer)
    other_customer = add_customer(db, "Synthetic Other")

    with pytest.raises(InquiryNotFoundError):
        service().create(db, conversation_id=uuid4())
    with pytest.raises(InquiryValidationError, match="different customer"):
        service().create(db, conversation_id=conversation.id, customer_id=other_customer.id)


def test_get_rejects_an_inquiry_in_another_tenant(db: Session) -> None:
    inquiry = service().create(db)

    with pytest.raises(InquiryNotFoundError):
        service().get(db, inquiry.id, tenant_id=uuid4())


def test_latest_inbound_message_prefers_the_newest_inbound(db: Session) -> None:
    customer = add_customer(db)
    contact = add_contact(db, customer)
    conversation = add_conversation(db, customer=customer, contact=contact)
    older = add_message(
        db,
        conversation,
        "older message",
        occurred_at=datetime(2026, 10, 20, 9, 0, tzinfo=UTC),
    )
    newest = add_message(
        db,
        conversation,
        "newest message",
        occurred_at=datetime(2026, 10, 21, 9, 0, tzinfo=UTC),
    )
    add_message(
        db,
        conversation,
        "our reply",
        direction="outbound",
        occurred_at=datetime(2026, 10, 22, 9, 0, tzinfo=UTC),
    )
    inquiry = service().create(db, conversation_id=conversation.id)

    message = service().latest_inbound_message(db, inquiry)

    assert message.id == newest.id
    assert message.id != older.id
    assert service().latest_inbound_message(db, inquiry, message_id=older.id).id == older.id


# --------------------------------------------------------------------------
# Parse persistence and workflow
# --------------------------------------------------------------------------


def test_parse_appends_versions_and_moves_received_to_needs_info_to_parsed(
    db: Session,
) -> None:
    customer = add_customer(db)
    contact = add_contact(db, customer)
    conversation = add_conversation(db, customer=customer, contact=contact)
    add_message(db, conversation)
    inquiry = service().create(db, conversation_id=conversation.id)
    svc = service()

    first = svc.record_parse(
        db,
        inquiry_id=inquiry.id,
        outcome=parsed_outcome(package_requirement(), message=MESSAGE, sources=PACKAGE_SOURCES),
    )

    assert first.inquiry.state == "NEEDS_INFO"
    assert first.requires_info is True
    assert [gap.field for gap in first.missing] == [
        "rooms",
        "origin_city",
        "end_date",
        "budget_paise",
        "confirmed_dates",
    ]
    assert first.requirement.version == 1
    assert first.requirement.prompt_name == "inquiry_parser"
    assert first.requirement.prompt_version == 1
    assert first.requirement.provider == "fake"
    assert first.requirement.model == "fake-1"
    assert first.requirement.llm_call_id is not None

    with pytest.raises(InquiryNeedsInfoError, match="rooms"):
        svc.assert_ready_for_search(db, inquiry.id)

    second = svc.record_parse(
        db,
        inquiry_id=inquiry.id,
        outcome=parsed_outcome(
            package_requirement(rooms=2),
            message=MESSAGE_WITH_ROOMS,
            sources={**PACKAGE_SOURCES, "rooms": ["2 rooms"]},
        ),
    )

    assert second.inquiry.state == "PARSED"
    assert second.requires_info is False
    assert second.requirement.version == 2
    assert svc.assert_ready_for_search(db, inquiry.id).state == "PARSED"

    versions = svc.requirements(db, inquiry.id)
    assert [row.version for row in versions] == [1, 2]
    assert versions[0].data["rooms"] is None
    assert versions[1].data["rooms"] == 2
    assert "inquiry.parsed" in audit_actions(db)


def test_parse_refuses_a_closed_inquiry(db: Session) -> None:
    inquiry = service().create(db)
    inquiry.state = "APPROVED"
    db.flush()

    with pytest.raises(InquiryStateError, match="APPROVED"):
        service().record_parse(
            db,
            inquiry_id=inquiry.id,
            outcome=parsed_outcome(package_requirement(), message=MESSAGE, sources=PACKAGE_SOURCES),
        )
    assert service().requirements(db, inquiry.id) == []


def test_parse_refuses_an_asserted_field_without_evidence(db: Session) -> None:
    inquiry = service().create(db)
    incomplete = parsed_outcome(
        flight_requirement(),
        message=FLIGHT_MESSAGE,
        sources={key: value for key, value in FLIGHT_SOURCES.items() if key != "adults"},
    )

    with pytest.raises(SilentAssumptionError, match="adults"):
        service().record_parse(db, inquiry_id=inquiry.id, outcome=incomplete)

    assert service().requirements(db, inquiry.id) == []
    assert service().get(db, inquiry.id).state == "RECEIVED"


def test_requirement_update_is_blocked(db: Session) -> None:
    inquiry = service().create(db)
    record = service().record_parse(
        db,
        inquiry_id=inquiry.id,
        outcome=parsed_outcome(
            flight_requirement(), message=FLIGHT_MESSAGE, sources=FLIGHT_SOURCES
        ),
    )
    record.requirement.data = {"tampered": True}

    with pytest.raises(AppendOnlyError, match="append-only"):
        db.flush()


def test_requirement_delete_is_blocked(db: Session) -> None:
    inquiry = service().create(db)
    record = service().record_parse(
        db,
        inquiry_id=inquiry.id,
        outcome=parsed_outcome(
            flight_requirement(), message=FLIGHT_MESSAGE, sources=FLIGHT_SOURCES
        ),
    )

    db.delete(record.requirement)

    with pytest.raises(AppendOnlyError, match="append-only"):
        db.flush()


# --------------------------------------------------------------------------
# Duplicate detection
# --------------------------------------------------------------------------


def test_duplicate_contact_fingerprint_is_flagged_for_a_human_and_not_merged(
    db: Session,
) -> None:
    first_customer = add_customer(db, "Synthetic First")
    first_contact = add_contact(db, first_customer, value="+919000000077")
    first_conversation = add_conversation(db, customer=first_customer, contact=first_contact)
    add_message(db, first_conversation, FLIGHT_MESSAGE)
    first = service().create(db, conversation_id=first_conversation.id)
    service().record_parse(
        db,
        inquiry_id=first.id,
        outcome=parsed_outcome(
            flight_requirement(), message=FLIGHT_MESSAGE, sources=FLIGHT_SOURCES
        ),
    )

    second_customer = add_customer(db, "Synthetic Second")
    second_contact = add_contact(db, second_customer, value="+919000000077")
    second_conversation = add_conversation(db, customer=second_customer, contact=second_contact)
    add_message(db, second_conversation, FLIGHT_MESSAGE)
    second = service().create(db, conversation_id=second_conversation.id)

    record = service().record_parse(
        db,
        inquiry_id=second.id,
        outcome=parsed_outcome(
            flight_requirement(), message=FLIGHT_MESSAGE, sources=FLIGHT_SOURCES
        ),
    )

    assert record.duplicates
    evidence = record.duplicates[0]
    assert evidence.inquiry_id == first.id
    assert evidence.reasons == ("same_contact_fingerprint",)
    assert second.duplicate_of_inquiry_id == first.id
    assert second.duplicate_evidence[0]["inquiry_id"] == str(first.id)
    assert first_customer.merged_into_customer_id is None
    assert second_customer.merged_into_customer_id is None
    task = db.execute(
        select(HumanTask).where(HumanTask.task_type == "inquiry.duplicate_suspected")
    ).scalar_one()
    assert task.subject_id == second.id
    assert task.assignee_role == "agent"


def test_same_conversation_is_flagged_even_without_a_customer(db: Session) -> None:
    conversation = add_conversation(db)
    first = service().create(db, conversation_id=conversation.id)
    service().record_parse(
        db,
        inquiry_id=first.id,
        outcome=parsed_outcome(
            flight_requirement(), message=FLIGHT_MESSAGE, sources=FLIGHT_SOURCES
        ),
    )
    second = service().create(db, conversation_id=conversation.id)

    record = service().record_parse(
        db,
        inquiry_id=second.id,
        outcome=parsed_outcome(
            flight_requirement(), message=FLIGHT_MESSAGE, sources=FLIGHT_SOURCES
        ),
    )

    assert record.duplicates[0].inquiry_id == first.id
    assert record.duplicates[0].reasons == ("same_conversation",)


def test_duplicate_detection_ignores_closed_inquiries(db: Session) -> None:
    conversation = add_conversation(db)
    closed = service().create(db, conversation_id=conversation.id)
    closed.state = "CLOSED"
    db.flush()
    inquiry = service().create(db, conversation_id=conversation.id)

    record = service().record_parse(
        db,
        inquiry_id=inquiry.id,
        outcome=parsed_outcome(
            flight_requirement(), message=FLIGHT_MESSAGE, sources=FLIGHT_SOURCES
        ),
    )

    assert record.duplicates == ()


# --------------------------------------------------------------------------
# Clarifying questions
# --------------------------------------------------------------------------


def test_clarifying_questions_create_an_approval_and_a_draft_but_send_nothing(
    db: Session,
) -> None:
    customer = add_customer(db)
    contact = add_contact(db, customer)
    conversation = add_conversation(db, customer=customer, contact=contact)
    add_message(db, conversation)
    inquiry = service().create(db, conversation_id=conversation.id)
    requester = add_user(db)
    svc = service()
    svc.record_parse(
        db,
        inquiry_id=inquiry.id,
        outcome=parsed_outcome(package_requirement(), message=MESSAGE, sources=PACKAGE_SOURCES),
    )

    result = svc.draft_clarifying_questions(db, inquiry_id=inquiry.id, requested_by=requester.id)

    assert result.draft.status == "draft"
    assert result.draft.sent_at is None
    assert result.draft.channel == "whatsapp"
    assert result.approval is not None
    assert result.draft.approval_id == result.approval.id
    assert result.approval.action_type == "message.clarifying_questions"
    assert result.approval.level == "A1"
    assert result.approval.status == "pending"
    assert "rooms" in result.codes
    assert "How many rooms do you need?" in result.draft.body
    assert "inquiry.clarifying_questions_drafted" in audit_actions(db)

    outbound = db.execute(select(Message).where(Message.direction == "outbound")).scalars().all()
    assert outbound == []
    raw_body = db.execute(
        text("SELECT body FROM message_drafts WHERE id = :id"), {"id": result.draft.id}
    ).scalar_one()
    assert raw_body.startswith("enc:v1:")
    assert "How many rooms" not in raw_body


def test_clarifying_questions_require_a_gap_and_a_channel(db: Session) -> None:
    no_conversation = service().create(db)
    requester = add_user(db)
    service().record_parse(
        db,
        inquiry_id=no_conversation.id,
        outcome=parsed_outcome(package_requirement(), message=MESSAGE, sources=PACKAGE_SOURCES),
    )

    with pytest.raises(InquiryValidationError, match="supported channel"):
        service().draft_clarifying_questions(
            db, inquiry_id=no_conversation.id, requested_by=requester.id
        )

    customer = add_customer(db)
    contact = add_contact(db, customer)
    conversation = add_conversation(db, customer=customer, contact=contact)
    add_message(db, conversation, COMPLETE_FLIGHT_MESSAGE)
    complete = service().create(db, conversation_id=conversation.id)
    service().record_parse(
        db,
        inquiry_id=complete.id,
        outcome=parsed_outcome(
            complete_flight_requirement(),
            message=COMPLETE_FLIGHT_MESSAGE,
            sources=COMPLETE_FLIGHT_SOURCES,
        ),
    )
    with pytest.raises(InquiryNoQuestionsError):
        service().draft_clarifying_questions(db, inquiry_id=complete.id, requested_by=requester.id)


def test_clarifying_questions_require_a_parsed_requirement(db: Session) -> None:
    inquiry = service().create(db)
    requester = add_user(db)

    with pytest.raises(InquiryNoRequirementError):
        service().draft_clarifying_questions(db, inquiry_id=inquiry.id, requested_by=requester.id)


def test_clarifying_questions_refuse_a_closed_inquiry(db: Session) -> None:
    inquiry = service().create(db)
    requester = add_user(db)
    service().record_parse(
        db,
        inquiry_id=inquiry.id,
        outcome=parsed_outcome(package_requirement(), message=MESSAGE, sources=PACKAGE_SOURCES),
    )
    inquiry.state = "CLOSED"
    db.flush()

    with pytest.raises(InquiryStateError, match="CLOSED"):
        service().draft_clarifying_questions(db, inquiry_id=inquiry.id, requested_by=requester.id)
    assert db.execute(select(MessageDraft)).scalars().all() == []


# --------------------------------------------------------------------------
# Customer matching
# --------------------------------------------------------------------------


def test_match_customer_by_contact_uses_the_keyed_fingerprint(db: Session) -> None:
    customer = add_customer(db)
    add_contact(db, customer, value="+919000000088", channel="whatsapp")

    matched = match_customer_by_contact(db, channel="WhatsApp", value="+919000000088")

    assert matched == customer.id
    assert match_customer_by_contact(db, channel="whatsapp", value="+919000000099") is None


def test_match_customer_by_contact_ignores_merged_customers(db: Session) -> None:
    target = add_customer(db, "Synthetic Target")
    merged = add_customer(db, "Synthetic Merged")
    add_contact(db, merged, value="+919000000089")
    merged.merged_into_customer_id = target.id
    db.flush()

    assert match_customer_by_contact(db, channel="whatsapp", value="+919000000089") is None


def test_match_customer_by_contact_rejects_unknown_channels_and_blank_values(
    db: Session,
) -> None:
    with pytest.raises(ValueError, match="unsupported channel"):
        match_customer_by_contact(db, channel="carrier_pigeon", value="x")
    with pytest.raises(ValueError, match="required"):
        match_customer_by_contact(db, channel="whatsapp", value="  ")


# --------------------------------------------------------------------------
# Listing
# --------------------------------------------------------------------------


def test_list_inquiries_filters_by_state_customer_and_tenant(db: Session) -> None:
    customer = add_customer(db)
    conversation = add_conversation(db, customer=customer)
    first = service().create(db, conversation_id=conversation.id)
    second = service().create(db)
    second.state = "NEEDS_INFO"
    db.flush()

    by_state = service().list_inquiries(db, state="NEEDS_INFO")
    by_customer = service().list_inquiries(db, customer_id=customer.id)

    assert [row.id for row in by_state] == [second.id]
    assert [row.id for row in by_customer] == [first.id]
    assert service().list_inquiries(db, tenant_id=uuid4()) == []


def test_requirements_are_tenant_scoped(db: Session) -> None:
    inquiry = service().create(db)
    service().record_parse(
        db,
        inquiry_id=inquiry.id,
        outcome=parsed_outcome(
            flight_requirement(), message=FLIGHT_MESSAGE, sources=FLIGHT_SOURCES
        ),
    )

    assert service().requirements(db, inquiry.id, tenant_id=uuid4()) == []
    with pytest.raises(InquiryNoRequirementError):
        service().latest_requirement(db, inquiry.id, tenant_id=uuid4())


def test_inquiry_is_flagged_but_never_assigned_a_duplicate_state(db: Session) -> None:
    inquiry = service().create(db)
    record = service().record_parse(
        db,
        inquiry_id=inquiry.id,
        outcome=parsed_outcome(
            flight_requirement(), message=FLIGHT_MESSAGE, sources=FLIGHT_SOURCES
        ),
    )

    assert record.duplicates == ()
    assert inquiry.duplicate_of_inquiry_id is None
    assert inquiry.duplicate_evidence == []


def test_approval_and_draft_are_linked_one_to_one(db: Session) -> None:
    inquiry = service().create(db)
    requester = add_user(db)
    service().record_parse(
        db,
        inquiry_id=inquiry.id,
        outcome=parsed_outcome(package_requirement(), message=MESSAGE, sources=PACKAGE_SOURCES),
    )

    result = service().draft_clarifying_questions(
        db, inquiry_id=inquiry.id, requested_by=requester.id, channel="email"
    )

    assert result.approval is not None
    linked = db.execute(
        select(MessageDraft).where(MessageDraft.approval_id == result.approval.id)
    ).scalar_one()
    assert linked.id == result.draft.id
    approval = db.get(ApprovalRequest, result.approval.id)
    assert approval is not None
    assert approval.subject_id == inquiry.id
    assert approval.proposed["question_codes"] == list(result.codes)


def test_duplicate_window_excludes_old_inquiries(db: Session) -> None:
    conversation = add_conversation(db)
    old = service().create(db, conversation_id=conversation.id)
    old.state = "PARSED"
    old.created_at = datetime.now(UTC) - timedelta(days=45)
    db.flush()
    inquiry = service().create(db, conversation_id=conversation.id)

    record = service().record_parse(
        db,
        inquiry_id=inquiry.id,
        outcome=parsed_outcome(
            flight_requirement(), message=FLIGHT_MESSAGE, sources=FLIGHT_SOURCES
        ),
    )

    assert record.duplicates == ()
    assert old.id != inquiry.id


def test_unknown_inquiry_raises_the_expected_not_found_errors(db: Session) -> None:
    missing = uuid4()
    with pytest.raises(InquiryNotFoundError):
        service().get(db, missing)
    with pytest.raises(InquiryNoRequirementError):
        service().latest_requirement(db, missing)
