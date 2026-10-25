"""Message intake and tenant-scoped timeline queries."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from travelos.channels.models import Conversation, Message
from travelos.core.audit import Actor, AuditWriter
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.crm.models import Contact, Customer

_CHANNELS = frozenset(
    {"whatsapp", "email", "sms", "phone", "instagram", "facebook", "google_business", "web_form"}
)


@dataclass(frozen=True, slots=True)
class InboundMessage:
    channel: str
    external_id: str
    external_thread_id: str
    body: str
    occurred_at: datetime
    media_refs: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class InboundResult:
    message: Message
    conversation: Conversation
    duplicate: bool


class ChannelValidationError(ValueError):
    """Inbound message is invalid or has an inconsistent CRM link."""


class ChannelService:
    def __init__(self, audit: AuditWriter | None = None) -> None:
        self._audit = audit or AuditWriter()

    def receive(
        self,
        db: Session,
        *,
        event: InboundMessage,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        customer_id: UUID | None = None,
        contact_id: UUID | None = None,
    ) -> InboundResult:
        if event.channel not in _CHANNELS:
            raise ChannelValidationError("unsupported channel")
        if not event.external_id.strip() or not event.external_thread_id.strip():
            raise ChannelValidationError("external identifiers are required")
        if len(event.external_id) > 255 or len(event.external_thread_id) > 255:
            raise ChannelValidationError("external identifier is too long")
        if len(event.body) > 100_000 or len(event.media_refs) > 20:
            raise ChannelValidationError("message exceeds configured limits")
        normalized_time = event.occurred_at
        if normalized_time.tzinfo is None:
            raise ChannelValidationError("occurred_at must include a timezone")
        normalized_time = normalized_time.astimezone(UTC)

        if customer_id is not None:
            customer = db.execute(
                select(Customer.id).where(
                    Customer.id == customer_id,
                    Customer.tenant_id == tenant_id,
                    Customer.merged_into_customer_id.is_(None),
                )
            ).scalar_one_or_none()
            if customer is None:
                raise ChannelValidationError("customer is unavailable")
        if contact_id is not None:
            contact = db.execute(
                select(Contact).where(Contact.id == contact_id, Contact.tenant_id == tenant_id)
            ).scalar_one_or_none()
            if contact is None or contact.channel != event.channel:
                raise ChannelValidationError("contact is unavailable for this channel")
            if customer_id is not None and contact.customer_id != customer_id:
                raise ChannelValidationError("contact does not belong to customer")
            customer_id = contact.customer_id

        conversation_insert = (
            insert(Conversation)
            .values(
                tenant_id=tenant_id,
                customer_id=customer_id,
                contact_id=contact_id,
                channel=event.channel,
                external_thread_id=event.external_thread_id.strip(),
                last_inbound_at=normalized_time,
            )
            .on_conflict_do_nothing(constraint="uq_conversations_tenant_thread")
        )
        db.execute(conversation_insert)
        conversation = db.execute(
            select(Conversation)
            .where(
                Conversation.tenant_id == tenant_id,
                Conversation.channel == event.channel,
                Conversation.external_thread_id == event.external_thread_id.strip(),
            )
            .with_for_update()
        ).scalar_one()
        if (
            conversation.customer_id is not None
            and customer_id is not None
            and conversation.customer_id != customer_id
        ):
            raise ChannelValidationError("thread is already linked to another customer")
        if (
            conversation.contact_id is not None
            and contact_id is not None
            and conversation.contact_id != contact_id
        ):
            raise ChannelValidationError("thread is already linked to another contact")
        if conversation.customer_id is None and customer_id is not None:
            conversation.customer_id = customer_id
        if conversation.contact_id is None and contact_id is not None:
            conversation.contact_id = contact_id
        if conversation.last_inbound_at is None or normalized_time > conversation.last_inbound_at:
            conversation.last_inbound_at = normalized_time
        db.flush()

        message_insert = (
            insert(Message)
            .values(
                tenant_id=tenant_id,
                conversation_id=conversation.id,
                direction="inbound",
                channel=event.channel,
                body=event.body,
                media_refs=list(event.media_refs),
                external_id=event.external_id.strip(),
                occurred_at=normalized_time,
            )
            .on_conflict_do_nothing(constraint="uq_messages_tenant_external")
            .returning(Message.id)
        )
        message_id = db.execute(message_insert).scalar_one_or_none()
        duplicate = message_id is None
        message = db.execute(
            select(Message).where(
                Message.tenant_id == tenant_id,
                Message.channel == event.channel,
                Message.external_id == event.external_id.strip(),
            )
        ).scalar_one()
        self._audit.record(
            db,
            action="channel.inbound_received" if not duplicate else "channel.inbound_duplicate",
            actor=Actor.system(),
            subject_type="message",
            subject_id=message.id,
            after={
                "channel": event.channel,
                "conversation_id": conversation.id,
                "duplicate": duplicate,
            },
        )
        return InboundResult(message=message, conversation=conversation, duplicate=duplicate)

    @staticmethod
    def timeline(
        db: Session,
        *,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        customer_id: UUID | None = None,
        contact_id: UUID | None = None,
        channel: str | None = None,
        limit: int = 50,
    ) -> list[Message]:
        statement = (
            select(Message)
            .join(Conversation, Message.conversation_id == Conversation.id)
            .where(Message.tenant_id == tenant_id, Conversation.tenant_id == tenant_id)
        )
        if customer_id is not None:
            statement = statement.where(Conversation.customer_id == customer_id)
        if contact_id is not None:
            statement = statement.where(Conversation.contact_id == contact_id)
        if channel is not None:
            statement = statement.where(Message.channel == channel)
        return list(
            db.execute(
                statement.order_by(Message.occurred_at.desc(), Message.id.desc()).limit(limit)
            )
            .scalars()
            .all()
        )
