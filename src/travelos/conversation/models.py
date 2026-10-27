"""Handoff records that pair a conversation with a human task (task 011b).

The generic ``human_tasks`` row is what staff see in the queue. This table keeps
the reason codes and the encrypted conversation summary next to it, so the queue
can show why a conversation was escalated without putting customer text in an
unencrypted task title.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import ForeignKey, Index, Integer, UniqueConstraint, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from travelos.core.db import BaseTable
from travelos.security.encryption import EncryptedString


class ConversationHandoff(BaseTable):
    __tablename__ = "conversation_handoffs"

    inquiry_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("inquiries.id", ondelete="CASCADE"), nullable=False
    )
    conversation_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("conversations.id", ondelete="SET NULL")
    )
    human_task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("human_tasks.id", ondelete="CASCADE"), nullable=False
    )
    # Reason codes only (anger, complaint, ...). The human-readable evidence
    # lives inside ``summary``, which is encrypted.
    triggers: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )
    summary: Mapped[str] = mapped_column(EncryptedString(), nullable=False)
    summary_version: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    created_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        UniqueConstraint("human_task_id", name="uq_conversation_handoffs_human_task_id"),
        Index(
            "ix_conversation_handoffs_tenant_inquiry",
            "tenant_id",
            "inquiry_id",
            "created_at",
        ),
    )
