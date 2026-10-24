"""Append-only metadata for every LLM provider call.

One row per actual provider invocation (including repairs and failed attempts),
plus a row for a cache hit or a blocked call. Prompt text is stored redacted:
placeholders remain placeholders. Personal data, API keys and raw responses are
never written here.

The table is append-only in the ORM and in PostgreSQL, matching ``audit_log``.
Retention and deletion are an owner decision; see docs/BACKLOG.md.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.engine import Connection
from sqlalchemy.orm import Mapped, Mapper, mapped_column

from travelos.core.db import BaseTable
from travelos.core.errors import AppendOnlyError


class LLMCall(BaseTable):
    __tablename__ = "llm_calls"

    actor_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="SET NULL")
    )
    provider: Mapped[str | None] = mapped_column(String(80))
    model: Mapped[str | None] = mapped_column(String(160))
    prompt_name: Mapped[str] = mapped_column(String(120), nullable=False)
    prompt_version: Mapped[int] = mapped_column(Integer, nullable=False)
    data_class: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    input_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_paise: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    currency: Mapped[str] = mapped_column(
        CHAR(3), nullable=False, default="INR", server_default="INR"
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    repairs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    request: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    response: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    request_hash: Mapped[str | None] = mapped_column(String(64))
    response_hash: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    metadata_json: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)

    __table_args__ = (
        CheckConstraint(
            "status IN ('success', 'schema_error', 'provider_error', "
            "'all_providers_failed', 'policy_blocked', 'budget_blocked')",
            name="status",
        ),
        CheckConstraint("data_class IN ('synthetic', 'customer', 'public')", name="data_class"),
        CheckConstraint("prompt_version >= 1", name="prompt_version_positive"),
        CheckConstraint("input_tokens >= 0 AND output_tokens >= 0", name="tokens_non_negative"),
        CheckConstraint("cost_paise >= 0", name="cost_non_negative"),
        CheckConstraint("latency_ms >= 0", name="latency_non_negative"),
        CheckConstraint("repairs >= 0", name="repairs_non_negative"),
        Index("ix_llm_calls_prompt", "tenant_id", "prompt_name", "created_at"),
        Index("ix_llm_calls_status", "status", "created_at"),
        Index("ix_llm_calls_provider", "provider", "created_at"),
    )


def _block_update(_mapper: Mapper[LLMCall], _connection: Connection, _target: LLMCall) -> None:
    raise AppendOnlyError("llm_calls is append-only: UPDATE is forbidden")


def _block_delete(_mapper: Mapper[LLMCall], _connection: Connection, _target: LLMCall) -> None:
    raise AppendOnlyError("llm_calls is append-only: DELETE is forbidden")


event.listen(LLMCall, "before_update", _block_update)
event.listen(LLMCall, "before_delete", _block_delete)
