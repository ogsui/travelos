"""Persistent timers used by workflow SLAs, expiries and reminders."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    String,
    UniqueConstraint,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from travelos.core.db import BaseTable


class WorkflowTimer(BaseTable):
    """One active timer per (tenant, name, subject).

    Rescheduling re-arms the existing row instead of inserting a new one, so a
    delayed workflow cannot accumulate duplicate reminders. When a timer fires
    it is marked ``fired``; the outbox event is the durable signal that work
    should happen.
    """

    __tablename__ = "workflow_timers"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    subject_type: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_id: Mapped[UUID] = mapped_column(PG_UUID(as_uuid=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="scheduled", server_default="scheduled"
    )
    fired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cancelled_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "name",
            "subject_type",
            "subject_id",
            name="uq_workflow_timers_subject",
        ),
        CheckConstraint(
            "status IN ('scheduled', 'fired', 'cancelled')",
            name="status",
        ),
        Index("ix_workflow_timers_status_due", "status", "due_at"),
        Index("ix_workflow_timers_subject", "subject_type", "subject_id"),
    )
