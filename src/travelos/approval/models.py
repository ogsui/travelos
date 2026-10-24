"""Approval request table.

A request records the AI/system proposal, who proposed it, the reviewer(s) and
the final payload after approve/edit/reject. The proposed-versus-final diff is
also written to the append-only audit log by ``ApprovalService``.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from travelos.core.db import BaseTable


class ApprovalRequest(BaseTable):
    __tablename__ = "approval_requests"

    action_type: Mapped[str] = mapped_column(String(120), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    requested_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    level: Mapped[str] = mapped_column(String(2), nullable=False)
    proposed: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    final: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str | None] = mapped_column(Text)
    context: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    requires_four_eyes: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    approvals_needed: Mapped[int] = mapped_column(
        Integer, nullable=False, default=1, server_default=text("1")
    )
    approvals_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    reviewer_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    second_reviewer_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    escalation_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )

    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'approved', 'edited', 'rejected', 'expired')",
            name="status",
        ),
        CheckConstraint("level IN ('A0', 'A1', 'A2', 'A3')", name="level"),
        CheckConstraint("approvals_needed IN (1, 2)", name="approvals_needed"),
        CheckConstraint(
            "approvals_count >= 0 AND approvals_count <= approvals_needed",
            name="approvals_count",
        ),
        CheckConstraint(
            "status <> 'approved' OR approvals_count = approvals_needed",
            name="approved_needs_all_approvals",
        ),
        CheckConstraint("escalation_count >= 0", name="escalation_non_negative"),
        Index("ix_approval_requests_status", "tenant_id", "status", "created_at"),
        Index("ix_approval_requests_action", "tenant_id", "action_type", "status"),
        Index("ix_approval_requests_subject", "subject_type", "subject_id"),
        Index("ix_approval_requests_expires", "status", "expires_at"),
    )
