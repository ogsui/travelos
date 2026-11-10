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
from travelos.quotes.models import LineKind, QuoteState

_STATES = ", ".join(f"'{state.value}'" for state in QuoteState)
_LINE_KINDS = ", ".join(f"'{kind.value}'" for kind in LineKind)
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


def _block_update(mapper: object, connection: object, target: object) -> None:
    del mapper, connection, target
    raise AppendOnlyError("quote revisions are append-only: UPDATE is forbidden")


def _block_delete(mapper: object, connection: object, target: object) -> None:
    del mapper, connection, target
    raise AppendOnlyError("quote revisions are append-only: DELETE is forbidden")


for _record in (QuoteRevisionRecord, QuoteLineRecord):
    event.listen(_record, "before_update", _block_update)
    event.listen(_record, "before_delete", _block_delete)

__all__ = ["QuoteLineRecord", "QuoteRecord", "QuoteRevisionRecord"]
