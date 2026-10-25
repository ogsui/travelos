"""Persisted conversations and normalized channel messages."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column, relationship

from travelos.core.db import BaseTable
from travelos.security.encryption import EncryptedString

_CHANNELS = (
    "'whatsapp', 'email', 'sms', 'phone', 'instagram', 'facebook', 'google_business', 'web_form'"
)


class Conversation(BaseTable):
    __tablename__ = "conversations"

    customer_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL")
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    external_thread_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", server_default="open"
    )
    last_inbound_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    messages: Mapped[list[Message]] = relationship(back_populates="conversation")

    __table_args__ = (
        CheckConstraint(f"channel IN ({_CHANNELS})", name="channel"),
        CheckConstraint("status IN ('open', 'closed')", name="status"),
        UniqueConstraint(
            "tenant_id", "channel", "external_thread_id", name="uq_conversations_tenant_thread"
        ),
        Index("ix_conversations_tenant_customer", "tenant_id", "customer_id"),
        Index("ix_conversations_tenant_contact", "tenant_id", "contact_id"),
    )


class Message(BaseTable):
    __tablename__ = "messages"

    conversation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="CASCADE"), nullable=False
    )
    direction: Mapped[str] = mapped_column(String(10), nullable=False)
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    body: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    media_refs: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    template_id: Mapped[str | None] = mapped_column(String(120))
    external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="received", server_default="received"
    )
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(f"channel IN ({_CHANNELS})", name="channel"),
        CheckConstraint("direction IN ('inbound', 'outbound')", name="direction"),
        CheckConstraint(
            "status IN ('received', 'queued', 'sent', 'delivered', 'failed')", name="status"
        ),
        UniqueConstraint("tenant_id", "channel", "external_id", name="uq_messages_tenant_external"),
        Index("ix_messages_tenant_occurred", "tenant_id", "occurred_at"),
        Index("ix_messages_conversation_occurred", "conversation_id", "occurred_at"),
    )

    conversation: Mapped[Conversation] = relationship(back_populates="messages")
