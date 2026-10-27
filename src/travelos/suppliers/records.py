"""Supplier registry, search runs and immutable price snapshots (task 012a2).

A ``price_snapshots`` row is the exact normalized offer a supplier returned for
one request. It is never updated or deleted: a newer price is a newer row. That
keeps a quote, PDF or price calculation attributable to the price it saw, even
after the supplier changes its fare. The PostgreSQL trigger in migration 0014
enforces this outside the ORM as well.
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
    UniqueConstraint,
    event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from travelos.core.db import BaseTable
from travelos.core.errors import AppendOnlyError

_SUPPLIER_STATUSES = "'active', 'disabled', 'unavailable'"
_SEARCH_STATUSES = "'running', 'ok', 'partial', 'failed'"


class Supplier(BaseTable):
    """A supplier the agency may search. Deleting one is not a workflow.

    ``credentials_ref`` is a pointer into the approved secret store, never a
    credential. ``adapter`` is the implementing class name, so a change is
    visible instead of silently swapping the integration.
    """

    __tablename__ = "suppliers"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    type: Mapped[str] = mapped_column(
        String(40), nullable=False, default="mixed", server_default="mixed"
    )
    adapter: Mapped[str] = mapped_column(String(120), nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="active", server_default="active"
    )
    credentials_ref: Mapped[str | None] = mapped_column(String(200))

    __table_args__ = (
        UniqueConstraint("tenant_id", "name", name="uq_suppliers_tenant_name"),
        CheckConstraint(f"status IN ({_SUPPLIER_STATUSES})", name="status"),
        Index("ix_suppliers_tenant_status", "tenant_id", "status"),
    )


class SupplierSearch(BaseTable):
    """One aggregated search run across suppliers.

    This row is operational history, not a frozen price: it records the query
    hash, timing, status and per-leg failures. The offers it returned are in
    ``price_snapshots``.
    """

    __tablename__ = "supplier_searches"

    inquiry_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("inquiries.id", ondelete="SET NULL")
    )
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        String(20), nullable=False, default="running", server_default="running"
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    offer_count: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default=text("0")
    )
    failures: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list, server_default=text("'[]'::jsonb")
    )

    __table_args__ = (
        CheckConstraint(f"status IN ({_SEARCH_STATUSES})", name="status"),
        CheckConstraint("offer_count >= 0", name="offer_count_non_negative"),
        CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at", name="finished_after_start"
        ),
        Index("ix_supplier_searches_tenant_created", "tenant_id", "created_at"),
        Index("ix_supplier_searches_request_hash", "request_hash"),
    )


class PriceSnapshot(BaseTable):
    """An immutable normalized offer, with its own expiry and request hash.

    ``payload`` is the JSON form of ``Offer``; the scalar columns are there for
    querying and reporting. ``expires_at`` is capped by the configured snapshot
    TTL, so a supplier's longer validity window cannot keep a stale price live.
    """

    __tablename__ = "price_snapshots"

    supplier_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("suppliers.id"), nullable=False
    )
    search_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("supplier_searches.id")
    )
    offer_id: Mapped[str] = mapped_column(String(200), nullable=False)
    product_type: Mapped[str] = mapped_column(String(20), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    net_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    taxes_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, default="INR")
    fare_rules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    request_hash: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        CheckConstraint("net_paise >= 0 AND taxes_paise >= 0", name="amounts_non_negative"),
        CheckConstraint("expires_at > fetched_at", name="expiry_after_fetch"),
        Index(
            "ix_price_snapshots_lookup",
            "tenant_id",
            "supplier_id",
            "request_hash",
            "expires_at",
        ),
        Index(
            "ix_price_snapshots_offer",
            "tenant_id",
            "supplier_id",
            "offer_id",
            "fetched_at",
        ),
    )


def _block_snapshot_update(mapper: object, connection: object, target: object) -> None:
    del mapper, connection, target
    raise AppendOnlyError("price_snapshots is append-only: UPDATE is forbidden")


def _block_snapshot_delete(mapper: object, connection: object, target: object) -> None:
    del mapper, connection, target
    raise AppendOnlyError("price_snapshots is append-only: DELETE is forbidden")


event.listen(PriceSnapshot, "before_update", _block_snapshot_update)
event.listen(PriceSnapshot, "before_delete", _block_snapshot_delete)
