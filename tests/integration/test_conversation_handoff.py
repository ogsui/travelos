"""Handoff triggers create human tasks with an encrypted summary (task 011b)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from travelos.channels.models import Conversation, Message
from travelos.conversation.errors import SalesNotFoundError, SalesValidationError
from travelos.conversation.handoff import ConversationHandoffService
from travelos.conversation.models import ConversationHandoff
from travelos.conversation.triggers import HandoffTrigger
from travelos.core.audit import AuditWriter
from travelos.core.clock import ManualClock
from travelos.core.models import HumanTask, User
from travelos.crm.fingerprints import ContactFingerprinter
from travelos.crm.models import Contact, Customer
from travelos.inquiry.models import Inquiry
from travelos.security.encryption import ENCRYPTED_PREFIX

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 27, 10, 0, tzinfo=UTC)


def add_user(db: Session, *, email: str = "agent@example.test") -> User:
    user = User(email=email, password_hash="x" * 20, role="agent")
    db.add(user)
    db.flush()
    return user


def add_customer(db: Session) -> Customer:
    customer = Customer(customer_type="family", display_name="Synthetic Family")
    db.add(customer)
    db.flush()
    return customer


def add_contact(db: Session, customer: Customer, *, language: str = "en") -> Contact:
    contact = Contact(
        customer_id=customer.id,
        channel="whatsapp",
        value="+919000000001",
        value_hash=ContactFingerprinter().fingerprint("whatsapp", "+919000000001"),
        is_primary=True,
        language=language,
    )
    db.add(contact)
    db.flush()
    return contact


def add_conversation(
    db: Session,
    *,
    customer: Customer,
    contact: Contact,
    channel: str = "whatsapp",
) -> Conversation:
    conversation = Conversation(
        customer_id=customer.id,
        contact_id=contact.id,
        channel=channel,
        external_thread_id=f"thread-{uuid4()}",
    )
    db.add(conversation)
    db.flush()
    return conversation


def add_messages(
    db: Session,
    conversation: Conversation,
    bodies: list[str],
    *,
    direction: str = "inbound",
    start: datetime | None = None,
) -> list[Message]:
    first = start or NOW - timedelta(minutes=30)
    messages: list[Message] = []
    for index, body in enumerate(bodies):
        message = Message(
            conversation_id=conversation.id,
            direction=direction,
            channel=conversation.channel,
            body=body,
            external_id=f"msg-{uuid4()}",
            occurred_at=first + timedelta(minutes=index),
        )
        db.add(message)
        messages.append(message)
    db.flush()
    return messages


def add_inquiry(
    db: Session,
    *,
    conversation: Conversation,
    customer: Customer,
    state: str = "RECEIVED",
    tenant_id: UUID | None = None,
) -> Inquiry:
    inquiry = Inquiry(
        customer_id=customer.id,
        conversation_id=conversation.id,
        state=state,
        **({"tenant_id": tenant_id} if tenant_id is not None else {}),
    )
    db.add(inquiry)
    db.flush()
    return inquiry


def make_inquiry(
    db: Session,
    bodies: list[str] | None = None,
    *,
    state: str = "RECEIVED",
) -> tuple[Inquiry, User]:
    user = add_user(db)
    customer = add_customer(db)
    contact = add_contact(db, customer)
    conversation = add_conversation(db, customer=customer, contact=contact)
    if bodies:
        add_messages(db, conversation, bodies)
    inquiry = add_inquiry(db, conversation=conversation, customer=customer, state=state)
    return inquiry, user


def service() -> ConversationHandoffService:
    return ConversationHandoffService(clock=ManualClock(NOW), audit=AuditWriter())


def audit_actions(db: Session) -> list[str]:
    db.flush()
    return [
        row[0] for row in db.execute(text("SELECT action FROM audit_log ORDER BY at, id")).all()
    ]


def task_count(db: Session) -> int:
    return db.execute(select(func.count()).select_from(HumanTask)).scalar_one()


def handoff_count(db: Session) -> int:
    return db.execute(select(func.count()).select_from(ConversationHandoff)).scalar_one()


def test_complaint_creates_a_high_priority_task_with_an_encrypted_summary(
    db: Session,
) -> None:
    inquiry, user = make_inquiry(
        db, ["I want to complain about the hotel. Our budget was ₹45,000."]
    )

    result = service().handoff(db, inquiry_id=inquiry.id, requested_by=user.id)

    assert result.created is True
    assert result.refreshed is False
    assert result.task is not None
    assert result.record is not None
    db.refresh(result.task)

    assert result.task.task_type == "conversation.handoff"
    assert result.task.subject_type == "inquiry"
    assert result.task.subject_id == inquiry.id
    assert result.task.status == "open"
    assert result.task.priority == "high"
    assert result.task.assignee_role == "owner"  # the message also contains ₹45,000
    assert result.task.title == "Handoff: complaint, booking value"
    assert "Synthetic Family" not in result.task.title

    assert result.record.human_task_id == result.task.id
    assert result.record.triggers == ["complaint", "booking_value"]
    assert "I want to complain" in result.record.summary
    assert "Synthetic Family" in result.record.summary
    assert "amount mentioned '₹45,000'" in result.record.summary

    raw = db.execute(
        text("SELECT summary FROM conversation_handoffs WHERE id = :id"),
        {"id": result.record.id},
    ).scalar_one()
    assert raw.startswith(ENCRYPTED_PREFIX)
    assert "Synthetic Family" not in raw
    assert "complain" not in raw
    assert "₹45,000" not in raw

    assert "sales.handoff_created" in audit_actions(db)
    assert task_count(db) == 1
    assert handoff_count(db) == 1
    assert (
        db.execute(
            select(func.count()).select_from(Message).where(Message.direction == "outbound")
        ).scalar_one()
        == 0
    )


def test_a_pure_complaint_is_high_priority_for_an_agent(db: Session) -> None:
    inquiry, user = make_inquiry(db, ["I want to complain about the hotel."])

    result = service().handoff(db, inquiry_id=inquiry.id, requested_by=user.id)

    assert result.task is not None
    assert result.task.priority == "high"
    assert result.task.assignee_role == "agent"
    assert result.task.title == "Handoff: complaint"
    assert result.record is not None
    assert result.record.triggers == ["complaint"]


def test_every_positive_booking_value_hands_off(db: Session) -> None:
    inquiry, user = make_inquiry(db)

    result = service().handoff(
        db,
        inquiry_id=inquiry.id,
        requested_by=user.id,
        booking_value_paise=100,
    )

    assert result.task is not None
    assert result.task.priority == "normal"
    assert result.task.assignee_role == "owner"
    assert result.record is not None
    assert result.record.triggers == ["booking_value"]
    assert "booking value ₹1.00" in result.record.summary


def test_zero_value_with_no_customer_signal_does_not_hand_off(db: Session) -> None:
    inquiry, user = make_inquiry(db, ["Two adults planning a trip in February."])

    result = service().handoff(
        db,
        inquiry_id=inquiry.id,
        requested_by=user.id,
        booking_value_paise=0,
    )

    assert result.created is False
    assert result.task is None
    assert result.record is None
    assert task_count(db) == 0
    assert handoff_count(db) == 0
    assert "sales.handoff_created" not in audit_actions(db)


def test_repeated_question_is_a_loop(db: Session) -> None:
    inquiry, user = make_inquiry(
        db,
        [
            "Do you have a visa for Thailand?",
            "Do you have a visa for Thailand?",
            "Do you have a visa for Thailand?",
        ],
    )

    result = service().handoff(db, inquiry_id=inquiry.id, requested_by=user.id)

    assert result.record is not None
    assert HandoffTrigger.LOOP.value in result.record.triggers
    assert HandoffTrigger.VISA.value in result.record.triggers


def test_second_call_is_idempotent(db: Session) -> None:
    inquiry, user = make_inquiry(db, ["I want to complain."])
    handoff = service()

    first = handoff.handoff(db, inquiry_id=inquiry.id, requested_by=user.id)
    second = handoff.handoff(db, inquiry_id=inquiry.id, requested_by=user.id)

    assert first.created is True
    assert second.created is False
    assert second.refreshed is False
    assert second.task is not None
    assert first.task is not None
    assert second.task.id == first.task.id
    assert task_count(db) == 1
    assert handoff_count(db) == 1
    assert audit_actions(db).count("sales.handoff_created") == 1
    assert "sales.handoff_refreshed" not in audit_actions(db)


def test_a_new_trigger_refreshes_the_open_task(db: Session) -> None:
    inquiry, user = make_inquiry(db, ["Do I need a visa for Thailand?"])
    handoff = service()

    first = handoff.handoff(db, inquiry_id=inquiry.id, requested_by=user.id)
    assert first.task is not None
    assert first.task.assignee_role == "agent"
    assert first.task.priority == "normal"
    assert first.record is not None
    assert first.record.triggers == ["visa"]

    conversation = db.get(Conversation, inquiry.conversation_id)
    assert conversation is not None
    add_messages(db, conversation, ["Actually, I want a refund."], start=NOW)

    second = handoff.handoff(db, inquiry_id=inquiry.id, requested_by=user.id)
    assert second.created is False
    assert second.refreshed is True
    assert second.task is not None
    assert second.task.id == first.task.id
    db.refresh(second.task)
    assert second.task.assignee_role == "owner"
    assert second.task.priority == "high"
    assert second.task.title == "Handoff: visa, refund/cancellation"

    assert second.record is not None
    assert second.record.triggers == ["visa", "refund"]
    assert "New trigger(s) since the first handoff: refund" in second.record.summary

    assert task_count(db) == 1
    assert handoff_count(db) == 1
    assert "sales.handoff_refreshed" in audit_actions(db)


def test_a_done_task_allows_a_new_handoff(db: Session) -> None:
    inquiry, user = make_inquiry(db, ["I want to complain."])
    handoff = service()

    first = handoff.handoff(db, inquiry_id=inquiry.id, requested_by=user.id)
    assert first.task is not None
    first.task.status = "done"
    db.flush()

    second = handoff.handoff(db, inquiry_id=inquiry.id, requested_by=user.id)

    assert second.created is True
    assert second.task is not None
    assert second.task.id != first.task.id
    assert task_count(db) == 2
    assert handoff_count(db) == 2


def test_a_closed_inquiry_still_hands_off(db: Session) -> None:
    inquiry, user = make_inquiry(db, ["I want a refund for the cancelled booking."], state="CLOSED")

    result = service().handoff(db, inquiry_id=inquiry.id, requested_by=user.id)

    assert result.created is True
    assert result.record is not None
    assert result.record.triggers == ["refund"]


def test_other_tenants_cannot_hand_off(db: Session) -> None:
    inquiry, user = make_inquiry(db, ["I want to complain."])

    with pytest.raises(SalesNotFoundError):
        service().handoff(
            db,
            inquiry_id=inquiry.id,
            requested_by=user.id,
            tenant_id=uuid4(),
        )

    assert task_count(db) == 0


def test_invalid_booking_value_is_refused(db: Session) -> None:
    inquiry, user = make_inquiry(db, ["I want to complain."])

    with pytest.raises(SalesValidationError, match="booking value"):
        service().handoff(
            db,
            inquiry_id=inquiry.id,
            requested_by=user.id,
            booking_value_paise=-1,
        )


def test_an_inquiry_without_a_conversation_can_still_hand_off(db: Session) -> None:
    user = add_user(db)
    customer = add_customer(db)
    inquiry = Inquiry(customer_id=customer.id, state="RECEIVED")
    db.add(inquiry)
    db.flush()

    result = service().handoff(
        db,
        inquiry_id=inquiry.id,
        requested_by=user.id,
        booking_value_paise=100,
    )

    assert result.task is not None
    assert result.record is not None
    assert result.record.conversation_id is None
    assert "Channel: unknown" in result.record.summary
