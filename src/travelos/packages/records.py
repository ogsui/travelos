"""Persisted itineraries and their chosen offers (task 013b1).

An ``itineraries`` row is the reviewable output of the deterministic builder, and
``itinerary_items`` holds one chosen offer per row. The pure domain stays free of
SQLAlchemy: ``store.ItineraryStore`` converts between the two.

``snapshot_id`` is a foreign key to ``price_snapshots`` only when the caller can
name the exact immutable snapshot the offer came from. The normalized offer
payload is stored alongside it so the itinerary remains readable after the
supplier changes its price, and so a rebuild does not need a live search.
"""

from __future__ import annotations

from datetime import date
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Date,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from travelos.core.db import BaseTable

_TIERS = "'value', 'standard', 'premium'"
_TRIP_KINDS = "'family', 'corporate', 'group', 'individual'"
_COMPONENT_TYPES = "'flight', 'hotel', 'transfer', 'activity', 'visa'"
_UNIT_BASES = "'traveller', 'room', 'group'"


class ItineraryRecord(BaseTable):
    """One built itinerary, optionally tied to the inquiry that caused it."""

    __tablename__ = "itineraries"

    inquiry_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("inquiries.id", ondelete="SET NULL")
    )
    option_tier: Mapped[str] = mapped_column(
        String(20), nullable=False, default="standard", server_default="standard"
    )
    trip_kind: Mapped[str] = mapped_column(String(20), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date] = mapped_column(Date, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False, default="INR")
    total_cost_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    booked_rooms: Mapped[int] = mapped_column(Integer, nullable=False)
    party: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    occupancy: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    days: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint(f"option_tier IN ({_TIERS})", name="option_tier"),
        CheckConstraint(f"trip_kind IN ({_TRIP_KINDS})", name="trip_kind"),
        CheckConstraint("end_date >= start_date", name="dates_ordered"),
        CheckConstraint("total_cost_paise >= 0", name="total_cost_non_negative"),
        CheckConstraint("booked_rooms >= 1", name="booked_rooms_positive"),
        Index("ix_itineraries_tenant_created", "tenant_id", "created_at"),
        Index("ix_itineraries_inquiry", "tenant_id", "inquiry_id"),
    )


class ItineraryItemRecord(BaseTable):
    """One chosen offer inside an itinerary, with its explanation and cost."""

    __tablename__ = "itinerary_items"

    itinerary_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("itineraries.id", ondelete="CASCADE"),
        nullable=False,
    )
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot_id: Mapped[UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("price_snapshots.id", ondelete="SET NULL")
    )
    component_type: Mapped[str] = mapped_column(String(20), nullable=False)
    supplier: Mapped[str] = mapped_column(String(120), nullable=False)
    offer_id: Mapped[str] = mapped_column(String(200), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_basis: Mapped[str] = mapped_column(String(20), nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date)
    day_index: Mapped[int] = mapped_column(Integer, nullable=False)
    cost_paise: Mapped[int] = mapped_column(BigInteger, nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    reasons: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    explanation: Mapped[str] = mapped_column(Text, nullable=False)
    offer: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint(f"component_type IN ({_COMPONENT_TYPES})", name="component_type"),
        CheckConstraint(f"unit_basis IN ({_UNIT_BASES})", name="unit_basis"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("cost_paise >= 0", name="cost_non_negative"),
        CheckConstraint("day_index >= 1", name="day_index_positive"),
        CheckConstraint("position >= 0", name="position_non_negative"),
        CheckConstraint("end_date IS NULL OR end_date >= start_date", name="dates_ordered"),
        UniqueConstraint(
            "itinerary_id", "position", name="uq_itinerary_items_itinerary_id_position"
        ),
        Index("ix_itinerary_items_itinerary", "itinerary_id"),
        Index("ix_itinerary_items_snapshot", "snapshot_id"),
    )
