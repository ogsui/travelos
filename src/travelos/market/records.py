"""Immutable competitor price snapshots (task 014a2b1).

A row is one observation one site showed at one time, stored against the
canonical hash of the product it answers. It is never updated or deleted: a
correction is a newer row with a newer fetch time. That keeps a quote, a ceiling
calculation or a staff explanation attributable to the exact price it saw, even
after the site changes. The PostgreSQL trigger in migration 0016 enforces this
outside the ORM as well.

``payload`` is the JSON form of ``CompetitorPrice`` for an ``ok`` row; a blocked
or unavailable run stores a small status object instead, so "this site had no
price" is visible later rather than looking like a site nobody asked.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    String,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from travelos.core.db import BaseTable
from travelos.core.errors import AppendOnlyError


class CompetitorSnapshot(BaseTable):
    """One competitor observation, or one failed run, for one query."""

    __tablename__ = "competitor_snapshots"

    site: Mapped[str] = mapped_column(String(120), nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    price_paise: Mapped[int | None] = mapped_column(BigInteger)
    currency: Mapped[str | None] = mapped_column(CHAR(3))
    comparability_score: Mapped[int | None] = mapped_column(Integer)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    detail: Mapped[str | None] = mapped_column(String(500))
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    basis: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint("status IN ('ok', 'blocked', 'unavailable')", name="status"),
        CheckConstraint(
            "(status = 'ok') = (price_paise IS NOT NULL)",
            name="price_matches_status",
        ),
        CheckConstraint(
            "(price_paise IS NULL) = (currency IS NULL)",
            name="currency_matches_price",
        ),
        CheckConstraint("price_paise IS NULL OR price_paise >= 0", name="price_non_negative"),
        CheckConstraint(
            "comparability_score IS NULL "
            "OR (comparability_score >= 0 AND comparability_score <= 100)",
            name="score_range",
        ),
        Index("ix_competitor_snapshots_query", "tenant_id", "query_hash", "fetched_at"),
        Index("ix_competitor_snapshots_site", "tenant_id", "site", "fetched_at"),
    )


def _block_snapshot_update(mapper: object, connection: object, target: object) -> None:
    del mapper, connection, target
    raise AppendOnlyError("competitor_snapshots is append-only: UPDATE is forbidden")


def _block_snapshot_delete(mapper: object, connection: object, target: object) -> None:
    del mapper, connection, target
    raise AppendOnlyError("competitor_snapshots is append-only: DELETE is forbidden")


event.listen(CompetitorSnapshot, "before_update", _block_snapshot_update)
event.listen(CompetitorSnapshot, "before_delete", _block_snapshot_delete)
