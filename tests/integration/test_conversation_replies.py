"""Approval-gated sales drafts, templates and claim guard (task 011a)."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from travelos.approval.models import ApprovalRequest
from travelos.channels.models import Conversation, Message
from travelos.conversation.errors import SalesClosedError, SalesNotFoundError, SalesValidationError
from travelos.conversation.guard import UnsupportedClaimError
from travelos.conversation.service import SalesReplyService
from travelos.conversation.templates import MessageTemplate, TemplateNotApprovedError
from travelos.core.audit import AuditWriter
from travelos.core.models import User
from travelos.crm.fingerprints import ContactFingerprinter
from travelos.crm.models import Contact, Customer
from travelos.inquiry.models import Inquiry, MessageDraft
from travelos.security.encryption import ENCRYPTED_PREFIX

pytestmark = pytest.mark.integration

ACK_BODY = "Namaste! We have received your enquiry and will reply shortly."


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
    customer: Customer | None = None,
    contact: Contact | None = None,
    channel: str = "whatsapp",
) -> Conversation:
    conversation = Conversation(
        customer_id=customer.id if customer is not None else None,
        contact_id=contact.id if contact is not None else None,
        channel=channel,
        external_thread_id=f"thread-{uuid4()}",
    )
    db.add(conversation)
    db.flush()
    return conversation


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


def approved_templates() -> tuple[MessageTemplate, ...]:
    return (
        MessageTemplate(
            key="ack_test_en",
            kind="acknowledgement",
            language="en",
            body=ACK_BODY,
            owner_approved=True,
            notes="Synthetic test template.",
        ),
    )


def service() -> SalesReplyService:
    return SalesReplyService(audit=AuditWriter(), templates=approved_templates())


def audit_actions(db: Session) -> list[str]:
    db.flush()
    return [
        row[0] for row in db.execute(text("SELECT action FROM audit_log ORDER BY at, id")).all()
    ]


def make_inquiry(db: Session, *, state: str = "RECEIVED") -> tuple[Inquiry, User]:
    user = add_user(db)
    customer = add_customer(db)
    contact = add_contact(db, customer)
    conversation = add_conversation(db, customer=customer, contact=contact)
    inquiry = add_inquiry(db, conversation=conversation, customer=customer, state=state)
    return inquiry, user


def test_acknowledgement_creates_an_encrypted_draft_and_a_pending_approval(
    db: Session,
) -> None:
    inquiry, user = make_inquiry(db)

    result = service().draft_acknowledgement(db, inquiry_id=inquiry.id, requested_by=user.id)

    assert result.template_key == "ack_test_en"
    assert result.draft.kind == "acknowledgement"
    assert result.draft.template_key == "ack_test_en"
    assert result.draft.body == ACK_BODY
    assert result.draft.status == "draft"
    assert result.draft.sent_at is None
    assert result.draft.question_codes == []
    assert result.guard.passed is True
    assert result.approval is not None
    assert result.approval.action_type == "message.instant_ack"
    assert result.approval.status == "pending"
    assert result.approval.subject_id == inquiry.id
    assert "body" not in result.approval.proposed
    assert result.approval.proposed["draft_id"] == str(result.draft.id)
    assert result.approval.context["guard"]["claims"] == []

    raw = db.execute(
        text("SELECT body, kind, template_key FROM message_drafts WHERE id = :id"),
        {"id": result.draft.id},
    ).one()
    assert raw.body.startswith(ENCRYPTED_PREFIX)
    assert ACK_BODY not in raw.body
    assert raw.kind == "acknowledgement"
    assert raw.template_key == "ack_test_en"

    assert "sales.acknowledgement_drafted" in audit_actions(db)
    assert db.execute(select(func.count()).select_from(Message)).scalar_one() == 0


def test_unapproved_templates_are_never_used(db: Session) -> None:
    inquiry, user = make_inquiry(db)

    with pytest.raises(TemplateNotApprovedError):
        SalesReplyService(audit=AuditWriter()).draft_acknowledgement(
            db, inquiry_id=inquiry.id, requested_by=user.id
        )

    assert db.execute(select(func.count()).select_from(MessageDraft)).scalar_one() == 0
    assert db.execute(select(func.count()).select_from(ApprovalRequest)).scalar_one() == 0


def test_free_form_reply_needs_a_source_for_every_claim(db: Session) -> None:
    inquiry, user = make_inquiry(db)
    reply_service = service()

    with pytest.raises(UnsupportedClaimError) as caught:
        reply_service.draft_reply(
            db,
            inquiry_id=inquiry.id,
            requested_by=user.id,
            body="The fare is Rs 45,000 for two adults.",
        )

    assert caught.value.report.unsupported_kinds() == ("money",)
    assert db.execute(select(func.count()).select_from(MessageDraft)).scalar_one() == 0
    assert db.execute(select(func.count()).select_from(ApprovalRequest)).scalar_one() == 0


def test_a_sourced_claim_is_stored_with_its_source_kind(db: Session) -> None:
    inquiry, user = make_inquiry(db)

    result = service().draft_reply(
        db,
        inquiry_id=inquiry.id,
        requested_by=user.id,
        body="The fare is Rs 45,000 for two adults.",
        sources={"money": ["quote:rev_01"]},
    )

    assert result.draft.kind == "sales_reply"
    assert result.draft.template_key is None
    assert result.draft.body == "The fare is Rs 45,000 for two adults."
    assert result.guard.passed is True
    assert result.approval is not None
    assert result.approval.action_type == "message.sales_reply"
    assert result.approval.context["guard"]["claims"] == ["money"]
    assert result.approval.context["guard"]["sources"] == {"money": ["quote:rev_01"]}
    assert "sales.reply_drafted" in audit_actions(db)


def test_clean_free_form_reply_is_approval_gated(db: Session) -> None:
    inquiry, user = make_inquiry(db)

    result = service().draft_reply(
        db,
        inquiry_id=inquiry.id,
        requested_by=user.id,
        body="Thank you for the details. We will review them and come back to you.",
    )

    assert result.approval is not None
    assert result.approval.action_type == "message.sales_reply"
    assert result.approval.context["guard"]["claims"] == []
    assert db.execute(select(func.count()).select_from(Message)).scalar_one() == 0


def test_acknowledgement_uses_the_contact_language_with_english_fallback(
    db: Session,
) -> None:
    user = add_user(db)
    customer = add_customer(db)
    contact = add_contact(db, customer, language="hi")
    conversation = add_conversation(db, customer=customer, contact=contact)
    inquiry = add_inquiry(db, conversation=conversation, customer=customer)

    # The approved test registry has only English, so a Hindi contact falls back.
    result = service().draft_acknowledgement(db, inquiry_id=inquiry.id, requested_by=user.id)
    assert result.template_key == "ack_test_en"


def test_closed_inquiries_cannot_be_drafted(db: Session) -> None:
    inquiry, user = make_inquiry(db, state="CLOSED")

    with pytest.raises(SalesClosedError, match="CLOSED"):
        service().draft_acknowledgement(db, inquiry_id=inquiry.id, requested_by=user.id)


def test_inquiry_without_a_conversation_is_refused(db: Session) -> None:
    user = add_user(db)
    customer = add_customer(db)
    inquiry = Inquiry(customer_id=customer.id, state="RECEIVED")
    db.add(inquiry)
    db.flush()

    with pytest.raises(SalesValidationError, match="without a conversation"):
        service().draft_acknowledgement(db, inquiry_id=inquiry.id, requested_by=user.id)


def test_other_tenants_cannot_draft(db: Session) -> None:
    inquiry, user = make_inquiry(db)

    with pytest.raises(SalesNotFoundError):
        service().draft_acknowledgement(
            db,
            inquiry_id=inquiry.id,
            requested_by=user.id,
            tenant_id=uuid4(),
        )


def test_reply_body_limits_are_enforced(db: Session) -> None:
    inquiry, user = make_inquiry(db)

    with pytest.raises(SalesValidationError, match="body is required"):
        service().draft_reply(db, inquiry_id=inquiry.id, requested_by=user.id, body="   ")
    with pytest.raises(SalesValidationError, match="at most"):
        service().draft_reply(db, inquiry_id=inquiry.id, requested_by=user.id, body="x" * 4001)


def test_draft_timestamp_and_sent_flag_are_not_set(db: Session) -> None:
    inquiry, user = make_inquiry(db)

    result = service().draft_acknowledgement(db, inquiry_id=inquiry.id, requested_by=user.id)

    assert result.draft.created_at <= datetime.now(UTC)
    assert result.draft.sent_at is None
    assert result.draft.status == "draft"
