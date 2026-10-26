"""Inquiry, requirement-version and outbound-draft tables (task 010c).

A requirement is stored as an append-only version. A re-parse never edits an
earlier version, so a reviewer can always see exactly what the parser said and
what evidence it used. ``message_drafts`` holds a customer-facing draft that is
waiting for approval; no sender exists in this task.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from travelos.core.db import BaseTable
from travelos.core.errors import AppendOnlyError
from travelos.security.encryption import EncryptedString

_CHANNELS = (
    "'whatsapp', 'email', 'sms', 'phone', 'instagram', 'facebook', 'google_business', 'web_form'"
)
_STATES = (
    "'RECEIVED', 'PARSED', 'NEEDS_INFO', 'SEARCHING', 'PRICED', 'PENDING_APPROVAL', "
    "'APPROVED', 'QUOTE_SENT', 'NEGOTIATING', 'ACCEPTED', 'AWAITING_PAYMENT', 'PAID', "
    "'BOOKING', 'BOOKED', 'INVOICED', 'DOCS_SENT', 'TRAVELLING', 'COMPLETED', 'CLOSED', "
    "'REJECTED', 'EXPIRED', 'LOST', 'CANCELLED'"
)
_TRIP_TYPES = "'flight', 'package', 'hotel', 'visa', 'corporate', 'group', 'unknown'"


class Inquiry(BaseTable):
    """One customer request, owned by the inquiry workflow machine."""

    __tablename__ = "inquiries"

    customer_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("customers.id", ondelete="SET NULL")
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    source_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    state: Mapped[str] = mapped_column(
        String(24), nullable=False, default="RECEIVED", server_default="RECEIVED"
    )
    trip_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="unknown", server_default="unknown"
    )
    urgency: Mapped[str] = mapped_column(
        String(10), nullable=False, default="normal", server_default="normal"
    )
    assigned_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    lost_reason: Mapped[str | None] = mapped_column(String(255))
    # Set by deterministic duplicate detection; a human still decides. Merging a
    # customer remains a locked A1 action and never happens here.
    duplicate_of_inquiry_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("inquiries.id", ondelete="SET NULL")
    )
    duplicate_evidence: Mapped[list[dict[str, object]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    __table_args__ = (
        CheckConstraint(f"state IN ({_STATES})", name="state"),
        CheckConstraint(f"trip_type IN ({_TRIP_TYPES})", name="trip_type"),
        CheckConstraint("urgency IN ('low', 'normal', 'high')", name="urgency"),
        Index("ix_inquiries_tenant_state", "tenant_id", "state"),
        Index("ix_inquiries_tenant_customer", "tenant_id", "customer_id", "created_at"),
        Index("ix_inquiries_conversation", "conversation_id"),
        Index("ix_inquiries_duplicate_of", "duplicate_of_inquiry_id"),
    )


class RequirementRow(BaseTable):
    """One immutable version of a parsed requirement.

    ``data`` is the validated ``Requirement`` JSON. Evidence columns hold the
    per-field confidence, character spans and the gap list the parser produced.
    ``prompt_name``/``prompt_version`` keep eval results attributable to an exact
    prompt version.
    """

    __tablename__ = "requirements"

    inquiry_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("inquiries.id", ondelete="CASCADE"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    field_confidence: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    source_spans: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    missing_fields: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    prompt_name: Mapped[str | None] = mapped_column(String(80))
    prompt_version: Mapped[int | None] = mapped_column(Integer)
    provider: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(120))
    llm_call_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))

    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        UniqueConstraint("tenant_id", "inquiry_id", "version", name="uq_requirements_version"),
        Index("ix_requirements_inquiry_created", "inquiry_id", "created_at"),
    )


class MessageDraft(BaseTable):
    """A customer-facing draft awaiting approval. Never sent from this module."""

    __tablename__ = "message_drafts"

    inquiry_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("inquiries.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    contact_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("contacts.id", ondelete="SET NULL")
    )
    approval_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("approval_requests.id", ondelete="SET NULL"), unique=True
    )
    channel: Mapped[str] = mapped_column(String(20), nullable=False)
    body: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    question_codes: Mapped[list[str]] = mapped_column(
        JSON, nullable=False, default=list, server_default=text("'[]'")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="draft", server_default="draft"
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(f"channel IN ({_CHANNELS})", name="channel"),
        CheckConstraint("status IN ('draft', 'approved', 'rejected', 'sent')", name="status"),
        CheckConstraint("status <> 'sent' OR sent_at IS NOT NULL", name="sent_needs_timestamp"),
        Index("ix_message_drafts_tenant_inquiry", "tenant_id", "inquiry_id"),
        Index("ix_message_drafts_approval", "approval_id"),
    )


def _block_requirement_update(mapper: object, connection: object, target: object) -> None:
    del mapper, connection, target
    raise AppendOnlyError("requirements is append-only: UPDATE is forbidden")


def _block_requirement_delete(mapper: object, connection: object, target: object) -> None:
    del mapper, connection, target
    raise AppendOnlyError("requirements is append-only: DELETE is forbidden")


event.listen(RequirementRow, "before_update", _block_requirement_update)
event.listen(RequirementRow, "before_delete", _block_requirement_delete)
