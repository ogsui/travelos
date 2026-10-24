"""Platform tables: identity, audit, flags, autonomy, idempotency, outbox, tasks."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from travelos.core.db import BaseTable
from travelos.core.errors import AppendOnlyError
from travelos.security.encryption import EncryptedString


class User(BaseTable):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(320), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    mfa_secret: Mapped[str | None] = mapped_column(EncryptedString(), nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default=text("true")
    )
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("tenant_id", "email", name="uq_users_tenant_email"),
        CheckConstraint("role IN ('owner', 'agent', 'accountant', 'admin')", name="role"),
        Index("ix_users_role_active", "role", "is_active"),
    )


class AuditLog(BaseTable):
    """Append-only. Enforced by a PostgreSQL trigger and ORM listeners."""

    __tablename__ = "audit_log"

    actor_type: Mapped[str] = mapped_column(String(20), nullable=False)
    actor_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    action: Mapped[str] = mapped_column(String(120), nullable=False)
    subject_type: Mapped[str | None] = mapped_column(String(80))
    subject_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ip: Mapped[str | None] = mapped_column(String(45))
    at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("actor_type IN ('user', 'system', 'ai', 'service')", name="actor_type"),
        Index("ix_audit_log_subject", "subject_type", "subject_id"),
        Index("ix_audit_log_actor", "actor_type", "actor_id"),
        Index("ix_audit_log_action_at", "action", "at"),
    )


def _block_audit_update(
    _mapper: Mapper[AuditLog], _connection: Connection, _target: AuditLog
) -> None:
    raise AppendOnlyError("audit_log is append-only: UPDATE is forbidden")


def _block_audit_delete(
    _mapper: Mapper[AuditLog], _connection: Connection, _target: AuditLog
) -> None:
    raise AppendOnlyError("audit_log is append-only: DELETE is forbidden")


event.listen(AuditLog, "before_update", _block_audit_update)
event.listen(AuditLog, "before_delete", _block_audit_delete)


class FeatureFlag(BaseTable):
    __tablename__ = "feature_flags"

    key: Mapped[str] = mapped_column(String(120), nullable=False)
    value: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    scope: Mapped[str] = mapped_column(
        String(80), nullable=False, default="global", server_default="global"
    )
    description: Mapped[str | None] = mapped_column(String(500))
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "key", "scope", name="uq_feature_flags_key_scope"),
        Index("ix_feature_flags_key", "key"),
    )


class AutonomyPolicy(BaseTable):
    __tablename__ = "autonomy_policies"

    action_type: Mapped[str] = mapped_column(String(120), nullable=False)
    level: Mapped[str] = mapped_column(String(2), nullable=False)
    value_threshold_paise: Mapped[int] = mapped_column(
        BigInteger, nullable=False, default=0, server_default=text("0")
    )
    currency: Mapped[str] = mapped_column(
        CHAR(3), nullable=False, default="INR", server_default="INR"
    )
    requires_four_eyes: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    notes: Mapped[str | None] = mapped_column(Text)
    updated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    effective_from: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "action_type", name="uq_autonomy_policies_action"),
        CheckConstraint("level IN ('A0', 'A1', 'A2', 'A3')", name="level"),
        CheckConstraint("value_threshold_paise >= 0", name="value_threshold_non_negative"),
    )


class AutonomyMetric(BaseTable):
    __tablename__ = "autonomy_metrics"

    action_type: Mapped[str] = mapped_column(String(120), nullable=False)
    window_start: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    window_end: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    proposals: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    approved_unchanged: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default=text("0")
    )
    edited: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    rejected: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    incidents: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    __table_args__ = (
        UniqueConstraint(
            "tenant_id",
            "action_type",
            "window_start",
            "window_end",
            name="uq_autonomy_metrics_window",
        ),
        CheckConstraint("window_end >= window_start", name="window_order"),
        CheckConstraint(
            "proposals >= 0 AND approved_unchanged >= 0 AND edited >= 0 "
            "AND rejected >= 0 AND incidents >= 0",
            name="counts_non_negative",
        ),
    )


class IdempotencyKey(BaseTable):
    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(String(200), nullable=False)
    scope: Mapped[str] = mapped_column(String(80), nullable=False)
    response_hash: Mapped[str | None] = mapped_column(String(64))
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("key", "scope", name="uq_idempotency_keys_key_scope"),
        Index("ix_idempotency_keys_expires_at", "expires_at"),
    )


class OutboxEvent(BaseTable):
    __tablename__ = "outbox_events"

    event_type: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(
        JSONB, nullable=False, server_default=text("'{}'::jsonb")
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="pending", server_default="pending"
    )
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    available_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    last_error: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("status IN ('pending', 'processing', 'done', 'dead')", name="status"),
        CheckConstraint("attempts >= 0", name="attempts_non_negative"),
        Index("ix_outbox_events_status_available", "status", "available_at"),
    )


class HumanTask(BaseTable):
    __tablename__ = "human_tasks"

    task_type: Mapped[str] = mapped_column(String(80), nullable=False)
    subject_type: Mapped[str | None] = mapped_column(String(80))
    subject_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    assignee_role: Mapped[str | None] = mapped_column(String(20))
    assignee_user_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    priority: Mapped[str] = mapped_column(
        String(20), nullable=False, default="normal", server_default="normal"
    )
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="open", server_default="open"
    )

    __table_args__ = (
        CheckConstraint("priority IN ('low', 'normal', 'high', 'urgent')", name="priority"),
        CheckConstraint("status IN ('open', 'in_progress', 'done', 'cancelled')", name="status"),
        Index("ix_human_tasks_status_due", "status", "due_at"),
        Index("ix_human_tasks_assignee", "assignee_role", "status"),
    )


class BusinessRuleVersion(BaseTable):
    __tablename__ = "business_rule_versions"

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    yaml_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    activated_by: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    activated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("tenant_id", "version", name="uq_business_rule_versions_version"),
        UniqueConstraint("tenant_id", "yaml_hash", name="uq_business_rule_versions_hash"),
        CheckConstraint("version > 0", name="version_positive"),
    )
