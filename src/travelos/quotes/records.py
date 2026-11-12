"""Persisted quotes, revisions and price lines (task 016a).

``quotes`` is the mutable lifecycle row (state, current revision, expiry, accept
token hash). ``quote_revisions`` and ``quote_lines`` are append-only: a correction
is a new revision, never an edit, so a customer-facing price can always be
traced back to the exact lines that were shown.

A revision cites both the itinerary (item detail and snapshot references) and the
stored ``price_calculations`` row (the exact total and breakdown) through
``quote_revisions.price_calculation_id``, which is unique per tenant. The reverse
``price_calculations.quote_revision_id`` is a plain nullable hint, not a foreign key:
a calculation may be recorded before its revision exists, and a calculation recorded
inside the quote flow may name the revision id the store is about to insert.

``quote_price_overrides`` is the append-only owner decision that lets a revision
cite a non-``ok`` calculation (task 016c2c). It names the source calculation, the
owner's deterministic replacement calculation, the decision task it completed and
the reviewer, so a below-floor or uncompetitive price always has a stored reason.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from travelos.core.db import BaseTable
from travelos.core.errors import AppendOnlyError
from travelos.quotes.models import LineKind, QuoteEventKind, QuoteState

_STATES = ", ".join(f"'{state.value}'" for state in QuoteState)
_LINE_KINDS = ", ".join(f"'{kind.value}'" for kind in LineKind)
_EVENT_KINDS = ", ".join(f"'{kind.value}'" for kind in QuoteEventKind)
_EVENT_ACTORS = "'customer', 'staff', 'system'"
_OVERRIDE_STATUSES = "'below_floor', 'uncompetitive'"
_CURRENCY_PATTERN = "^[A-Z]{3}$"
_TOKEN_PATTERN = "^[0-9a-f]{64}$"


class QuoteRecord(BaseTable):
    """One quote option under an inquiry, with its lifecycle state."""

    __tablename__ = "quotes"

    inquiry_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("inquiries.id", ondelete="SET NULL")
    )
    current_revision: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    state: Mapped[str] = mapped_column(
        String(20), nullable=False, default=QuoteState.DRAFT.value, server_default="DRAFT"
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # The plaintext token is never stored; 016c fills this with a sha256.
    accept_token_hash: Mapped[str | None] = mapped_column(CHAR(64))

    __table_args__ = (
        CheckConstraint("current_revision >= 0", name="current_revision_non_negative"),
        CheckConstraint(f"state IN ({_STATES})", name="state"),
        CheckConstraint(
            f"accept_token_hash IS NULL OR accept_token_hash ~ '{_TOKEN_PATTERN}'",
            name="accept_token_hash_hex",
        ),
        UniqueConstraint(
            "tenant_id", "accept_token_hash", name="uq_quotes_tenant_accept_token_hash"
        ),
        Index("ix_quotes_tenant_state", "tenant_id", "state"),
        Index("ix_quotes_tenant_inquiry", "tenant_id", "inquiry_id"),
        Index("ix_quotes_tenant_expires", "tenant_id", "expires_at"),
    )


class QuoteRevisionRecord(BaseTable):
    """One immutable revision of a quote."""

    __tablename__ = "quote_revisions"

    quote_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("quotes.id", ondelete="CASCADE"),
        nullable=False,
    )
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    itinerary_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("itineraries.id", ondelete="RESTRICT"),
        nullable=False,
    )
    price_calculation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("price_calculations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    total_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, default="INR")
    pdf_ref: Mapped[str | None] = mapped_column(String(500))
    diff_from_previous: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    approval_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("approval_requests.id", ondelete="SET NULL")
    )
    pricing_override_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("quote_price_overrides.id", ondelete="RESTRICT"),
    )

    __table_args__ = (
        CheckConstraint("number >= 1", name="number_positive"),
        CheckConstraint("total_paise >= 0", name="total_non_negative"),
        CheckConstraint(f"currency ~ '{_CURRENCY_PATTERN}'", name="currency_code"),
        UniqueConstraint("quote_id", "number", name="uq_quote_revisions_quote_id_number"),
        UniqueConstraint(
            "tenant_id",
            "price_calculation_id",
            name="uq_quote_revisions_tenant_price_calculation_id",
        ),
        UniqueConstraint(
            "tenant_id",
            "pricing_override_id",
            name="uq_quote_revisions_tenant_pricing_override_id",
        ),
        Index("ix_quote_revisions_tenant_quote", "tenant_id", "quote_id"),
        Index("ix_quote_revisions_calculation", "price_calculation_id"),
    )


class QuoteLineRecord(BaseTable):
    """One exact price line shown to the customer in a revision."""

    __tablename__ = "quote_lines"

    revision_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("quote_revisions.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    snapshot_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("price_snapshots.id", ondelete="SET NULL")
    )
    amount_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, default="INR")

    __table_args__ = (
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint(f"kind IN ({_LINE_KINDS})", name="kind"),
        CheckConstraint(f"currency ~ '{_CURRENCY_PATTERN}'", name="currency_code"),
        UniqueConstraint("revision_id", "position", name="uq_quote_lines_revision_id_position"),
        Index("ix_quote_lines_revision", "revision_id"),
        Index("ix_quote_lines_snapshot", "snapshot_id"),
    )


class QuoteGroupRecord(BaseTable):
    """One staff-titled set of quote options under one inquiry."""

    __tablename__ = "quote_groups"

    inquiry_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("inquiries.id", ondelete="RESTRICT"),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    note: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("length(btrim(title)) > 0", name="title_not_blank"),
        Index("ix_quote_groups_tenant_inquiry", "tenant_id", "inquiry_id"),
    )


class QuoteGroupOptionRecord(BaseTable):
    """One ordered, labelled quote inside a group."""

    __tablename__ = "quote_group_options"

    group_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("quote_groups.id", ondelete="CASCADE"),
        nullable=False,
    )
    quote_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("quotes.id", ondelete="RESTRICT"),
        nullable=False,
    )
    label: Mapped[str] = mapped_column(String(80), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)

    __table_args__ = (
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint("length(btrim(label)) > 0", name="label_not_blank"),
        UniqueConstraint("group_id", "position", name="uq_quote_group_options_group_id_position"),
        UniqueConstraint("group_id", "label", name="uq_quote_group_options_group_id_label"),
        UniqueConstraint("quote_id", name="uq_quote_group_options_quote_id"),
        Index("ix_quote_group_options_tenant_group", "tenant_id", "group_id"),
    )


class QuotePriceOverrideRecord(BaseTable):
    """One owner decision that authorises a non-``ok`` price (task 016c2c).

    The owner completes the ``pricing.owner_decision`` task the routing step
    created and may choose a different customer total. The chosen total is itself
    a deterministic calculation (``assemble_owner_price``), cited here as
    ``authorised_calculation_id``, so a quote revision always points at a stored
    calculation with exact lines rather than at a typed number.

    The row is append-only: a later, different decision is a new override that
    cites the same source calculation. ``authorised_calculation_id`` is unique per
    tenant because one calculation can back at most one revision.
    """

    __tablename__ = "quote_price_overrides"

    source_calculation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("price_calculations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    authorised_calculation_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("price_calculations.id", ondelete="RESTRICT"),
        nullable=False,
    )
    decision_task_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("human_tasks.id", ondelete="RESTRICT"),
        nullable=False,
    )
    owner_user_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    floor_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    target_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    ceiling_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_customer_price_paise: Mapped[int | None] = mapped_column(BigInteger)
    owner_total_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, default="INR")

    __table_args__ = (
        CheckConstraint(f"status IN ({_OVERRIDE_STATUSES})", name="status"),
        CheckConstraint("length(btrim(reason)) > 0", name="reason_not_blank"),
        CheckConstraint("floor_paise >= 0", name="floor_non_negative"),
        CheckConstraint("target_paise >= 0", name="target_non_negative"),
        CheckConstraint("ceiling_paise >= 0", name="ceiling_non_negative"),
        CheckConstraint(
            "source_customer_price_paise IS NULL OR source_customer_price_paise >= 0",
            name="source_customer_price_non_negative",
        ),
        CheckConstraint("owner_total_paise >= 0", name="owner_total_non_negative"),
        CheckConstraint(f"currency ~ '{_CURRENCY_PATTERN}'", name="currency_code"),
        UniqueConstraint(
            "tenant_id",
            "authorised_calculation_id",
            name="uq_quote_price_overrides_tenant_authorised_calculation_id",
        ),
        Index(
            "ix_quote_price_overrides_tenant_source",
            "tenant_id",
            "source_calculation_id",
        ),
    )


class QuoteEventRecord(BaseTable):
    """One immutable customer-facing event in a quote's timeline.

    ``revision_number`` and ``total_paise`` are copied at the moment of the
    event so the timeline states what the customer saw and accepted even though
    it is read without joining the revision. Events are append-only.
    """

    __tablename__ = "quote_events"

    quote_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("quotes.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    total_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, default="INR")
    actor_type: Mapped[str] = mapped_column(
        String(20), nullable=False, default="system", server_default="system"
    )

    __table_args__ = (
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint("revision_number >= 1", name="revision_number_positive"),
        CheckConstraint(f"kind IN ({_EVENT_KINDS})", name="kind"),
        CheckConstraint("total_paise >= 0", name="total_non_negative"),
        CheckConstraint(f"currency ~ '{_CURRENCY_PATTERN}'", name="currency_code"),
        CheckConstraint(f"actor_type IN ({_EVENT_ACTORS})", name="actor_type"),
        UniqueConstraint("quote_id", "position", name="uq_quote_events_quote_id_position"),
        Index(
            "ix_quote_events_tenant_quote",
            "tenant_id",
            "quote_id",
            "position",
        ),
        Index("ix_quote_events_tenant_created", "tenant_id", "created_at"),
    )


def _block_update(mapper: object, connection: object, target: object) -> None:
    del mapper, connection, target
    raise AppendOnlyError("quote revisions are append-only: UPDATE is forbidden")


def _block_delete(mapper: object, connection: object, target: object) -> None:
    del mapper, connection, target
    raise AppendOnlyError("quote revisions are append-only: DELETE is forbidden")


def _block_group_update(mapper: object, connection: object, target: object) -> None:
    del mapper, connection, target
    raise AppendOnlyError("quote groups are append-only: UPDATE is forbidden")


def _block_group_delete(mapper: object, connection: object, target: object) -> None:
    del mapper, connection, target
    raise AppendOnlyError("quote groups are append-only: DELETE is forbidden")


def _block_event_update(mapper: object, connection: object, target: object) -> None:
    del mapper, connection, target
    raise AppendOnlyError("quote events are append-only: UPDATE is forbidden")


def _block_event_delete(mapper: object, connection: object, target: object) -> None:
    del mapper, connection, target
    raise AppendOnlyError("quote events are append-only: DELETE is forbidden")


def _block_override_update(mapper: object, connection: object, target: object) -> None:
    del mapper, connection, target
    raise AppendOnlyError("quote price overrides are append-only: UPDATE is forbidden")


def _block_override_delete(mapper: object, connection: object, target: object) -> None:
    del mapper, connection, target
    raise AppendOnlyError("quote price overrides are append-only: DELETE is forbidden")


for _record in (QuoteRevisionRecord, QuoteLineRecord):
    event.listen(_record, "before_update", _block_update)
    event.listen(_record, "before_delete", _block_delete)

for _group_record in (QuoteGroupRecord, QuoteGroupOptionRecord):
    event.listen(_group_record, "before_update", _block_group_update)
    event.listen(_group_record, "before_delete", _block_group_delete)

event.listen(QuoteEventRecord, "before_update", _block_event_update)
event.listen(QuoteEventRecord, "before_delete", _block_event_delete)

event.listen(QuotePriceOverrideRecord, "before_update", _block_override_update)
event.listen(QuotePriceOverrideRecord, "before_delete", _block_override_delete)

__all__ = [
    "QuoteEventRecord",
    "QuoteGroupOptionRecord",
    "QuoteGroupRecord",
    "QuoteLineRecord",
    "QuotePriceOverrideRecord",
    "QuoteRecord",
    "QuoteRevisionRecord",
]
