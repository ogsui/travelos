"""add itineraries and their chosen offers

Revision ID: 0015
Revises: 0014

An ``itineraries`` row is one deterministic build, and ``itinerary_items`` holds
the chosen offers with their explanations and supplier cost. Items may point at
an immutable ``price_snapshots`` row when the caller knows it; otherwise the
normalized offer payload is the provenance. No pricing, tax or booking columns
are added here.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_DEFAULT = sa.text("'00000000-0000-0000-0000-000000000001'::uuid")


def upgrade() -> None:
    op.create_table(
        "itineraries",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            server_default=_TENANT_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("inquiry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("option_tier", sa.String(length=20), server_default="standard", nullable=False),
        sa.Column("trip_kind", sa.String(length=20), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), server_default="INR", nullable=False),
        sa.Column("total_cost_paise", sa.BigInteger(), nullable=False),
        sa.Column("booked_rooms", sa.Integer(), nullable=False),
        sa.Column("party", postgresql.JSONB(), nullable=False),
        sa.Column("occupancy", postgresql.JSONB(), nullable=False),
        sa.Column("days", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "option_tier IN ('value', 'standard', 'premium')",
            name="ck_itineraries_option_tier",
        ),
        sa.CheckConstraint(
            "trip_kind IN ('family', 'corporate', 'group', 'individual')",
            name="ck_itineraries_trip_kind",
        ),
        sa.CheckConstraint("end_date >= start_date", name="ck_itineraries_dates_ordered"),
        sa.CheckConstraint("total_cost_paise >= 0", name="ck_itineraries_total_cost_non_negative"),
        sa.CheckConstraint("booked_rooms >= 1", name="ck_itineraries_booked_rooms_positive"),
        sa.ForeignKeyConstraint(["inquiry_id"], ["inquiries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_itineraries"),
    )
    op.create_index("ix_itineraries_tenant_id", "itineraries", ["tenant_id"])
    op.create_index("ix_itineraries_tenant_created", "itineraries", ["tenant_id", "created_at"])
    op.create_index("ix_itineraries_inquiry", "itineraries", ["tenant_id", "inquiry_id"])

    op.create_table(
        "itinerary_items",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            server_default=_TENANT_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("itinerary_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("component_type", sa.String(length=20), nullable=False),
        sa.Column("supplier", sa.String(length=120), nullable=False),
        sa.Column("offer_id", sa.String(length=200), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("unit_basis", sa.String(length=20), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("day_index", sa.Integer(), nullable=False),
        sa.Column("cost_paise", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("reasons", postgresql.JSONB(), nullable=False),
        sa.Column("explanation", sa.Text(), nullable=False),
        sa.Column("offer", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "component_type IN ('flight', 'hotel', 'transfer', 'activity', 'visa')",
            name="ck_itinerary_items_component_type",
        ),
        sa.CheckConstraint(
            "unit_basis IN ('traveller', 'room', 'group')",
            name="ck_itinerary_items_unit_basis",
        ),
        sa.CheckConstraint("quantity > 0", name="ck_itinerary_items_quantity_positive"),
        sa.CheckConstraint("cost_paise >= 0", name="ck_itinerary_items_cost_non_negative"),
        sa.CheckConstraint("day_index >= 1", name="ck_itinerary_items_day_index_positive"),
        sa.CheckConstraint("position >= 0", name="ck_itinerary_items_position_non_negative"),
        sa.CheckConstraint(
            "end_date IS NULL OR end_date >= start_date",
            name="ck_itinerary_items_dates_ordered",
        ),
        sa.ForeignKeyConstraint(["itinerary_id"], ["itineraries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["snapshot_id"], ["price_snapshots.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_itinerary_items"),
        sa.UniqueConstraint(
            "itinerary_id", "position", name="uq_itinerary_items_itinerary_id_position"
        ),
    )
    op.create_index("ix_itinerary_items_tenant_id", "itinerary_items", ["tenant_id"])
    op.create_index("ix_itinerary_items_itinerary", "itinerary_items", ["itinerary_id"])
    op.create_index("ix_itinerary_items_snapshot", "itinerary_items", ["snapshot_id"])


def downgrade() -> None:
    op.drop_index("ix_itinerary_items_snapshot", table_name="itinerary_items")
    op.drop_index("ix_itinerary_items_itinerary", table_name="itinerary_items")
    op.drop_index("ix_itinerary_items_tenant_id", table_name="itinerary_items")
    op.drop_table("itinerary_items")

    op.drop_index("ix_itineraries_inquiry", table_name="itineraries")
    op.drop_index("ix_itineraries_tenant_created", table_name="itineraries")
    op.drop_index("ix_itineraries_tenant_id", table_name="itineraries")
    op.drop_table("itineraries")
