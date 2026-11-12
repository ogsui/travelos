"""Append-only customer-facing quote timeline (task 016c1).

Every send, view, acceptance, rejection or expiry is one immutable row carrying
the revision and total the customer saw at that moment. ``position`` is a
per-quote sequence starting at 0, allocated while the caller holds the quote row
lock, so the timeline has a stable order even when two events share a timestamp.

This module never talks to a channel, never renders and never changes a price.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.money import Money
from travelos.quotes.errors import QuoteValidationError
from travelos.quotes.models import QuoteEventKind
from travelos.quotes.records import QuoteEventRecord, QuoteRecord

_ALLOWED_ACTORS = frozenset({"customer", "staff", "system"})
_INR = "INR"


@dataclass(frozen=True, slots=True)
class QuoteEventView:
    """One timeline entry, as stored."""

    position: int
    kind: QuoteEventKind
    revision_number: int
    total: Money
    actor_type: str
    at: datetime


class QuoteEventStore:
    """Writes and reads the append-only timeline of one tenant's quotes."""

    def __init__(self, db: Session, *, tenant_id: UUID = DEFAULT_TENANT_ID) -> None:
        self._db = db
        self._tenant_id = tenant_id

    def record(
        self,
        quote: QuoteRecord,
        *,
        kind: QuoteEventKind,
        revision_number: int,
        total: Money,
        actor_type: str,
        at: datetime,
    ) -> QuoteEventRecord:
        """Append one event. The caller must hold the quote row lock."""

        if quote.tenant_id != self._tenant_id:
            raise QuoteValidationError(
                f"quote {quote.id} does not belong to tenant {self._tenant_id}"
            )
        if revision_number < 1 or revision_number > quote.current_revision:
            raise QuoteValidationError(
                f"revision {revision_number} is not a stored revision of quote {quote.id}"
            )
        if total.currency != _INR or total.paise < 0:
            raise QuoteValidationError("quote event totals must be non-negative INR")
        if actor_type not in _ALLOWED_ACTORS:
            raise QuoteValidationError(f"unknown quote event actor: {actor_type!r}")
        if at.tzinfo is None or at.utcoffset() is None:
            raise QuoteValidationError("quote event time must be timezone-aware")

        row = QuoteEventRecord(
            tenant_id=self._tenant_id,
            quote_id=quote.id,
            position=self._next_position(quote.id),
            revision_number=revision_number,
            kind=kind.value,
            total_paise=total.paise,
            currency=total.currency,
            actor_type=actor_type,
            created_at=at.astimezone(UTC),
        )
        self._db.add(row)
        self._db.flush()
        return row

    def timeline(self, quote_id: UUID) -> tuple[QuoteEventView, ...]:
        """Every event for a quote, in the order it happened."""

        rows = self._db.execute(
            select(QuoteEventRecord)
            .where(
                QuoteEventRecord.tenant_id == self._tenant_id,
                QuoteEventRecord.quote_id == quote_id,
            )
            .order_by(QuoteEventRecord.position)
        ).scalars()
        return tuple(
            QuoteEventView(
                position=row.position,
                kind=QuoteEventKind(row.kind),
                revision_number=row.revision_number,
                total=Money(row.total_paise, row.currency),
                actor_type=row.actor_type,
                at=row.created_at,
            )
            for row in rows
        )

    def _next_position(self, quote_id: UUID) -> int:
        current = self._db.execute(
            select(func.max(QuoteEventRecord.position)).where(
                QuoteEventRecord.tenant_id == self._tenant_id,
                QuoteEventRecord.quote_id == quote_id,
            )
        ).scalar_one_or_none()
        return 0 if current is None else current + 1


__all__ = ["QuoteEventStore", "QuoteEventView"]
