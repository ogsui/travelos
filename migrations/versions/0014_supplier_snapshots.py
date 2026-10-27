"""add suppliers, supplier searches and immutable price snapshots

Revision ID: 0014
Revises: 0013

``price_snapshots`` is append-only: a PostgreSQL trigger reuses the existing
``travelos_block_append_only`` function, so an UPDATE or DELETE fails even from
raw SQL. A newer price is a newer row.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0013"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_DEFAULT = sa.text("'00000000-0000-0000-0000-000000000001'::uuid")


def upgrade() -> None:
    op.create_table(
        "suppliers",
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
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("type", sa.String(length=40), server_default="mixed", nullable=False),
        sa.Column("adapter", sa.String(length=120), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="active", nullable=False),
        sa.Column("credentials_ref", sa.String(length=200), nullable=True),
        sa.CheckConstraint(
            "status IN ('active', 'disabled', 'unavailable')",
            name="ck_suppliers_status",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_suppliers"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_suppliers_tenant_name"),
    )
    op.create_index("ix_suppliers_tenant_id", "suppliers", ["tenant_id"])
    op.create_index("ix_suppliers_tenant_status", "suppliers", ["tenant_id", "status"])

    op.create_table(
        "supplier_searches",
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
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="running", nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("offer_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "failures",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('running', 'ok', 'partial', 'failed')",
            name="ck_supplier_searches_status",
        ),
        sa.CheckConstraint(
            "offer_count >= 0", name="ck_supplier_searches_offer_count_non_negative"
        ),
        sa.CheckConstraint(
            "finished_at IS NULL OR finished_at >= started_at",
            name="ck_supplier_searches_finished_after_start",
        ),
        sa.ForeignKeyConstraint(["inquiry_id"], ["inquiries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_supplier_searches"),
    )
    op.create_index("ix_supplier_searches_tenant_id", "supplier_searches", ["tenant_id"])
    op.create_index(
        "ix_supplier_searches_tenant_created",
        "supplier_searches",
        ["tenant_id", "created_at"],
    )
    op.create_index("ix_supplier_searches_request_hash", "supplier_searches", ["request_hash"])

    op.create_table(
        "price_snapshots",
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
        sa.Column("supplier_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("search_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("offer_id", sa.String(length=200), nullable=False),
        sa.Column("product_type", sa.String(length=20), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("net_paise", sa.BigInteger(), nullable=False),
        sa.Column("taxes_paise", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), server_default="INR", nullable=False),
        sa.Column("fare_rules", postgresql.JSONB(), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.CheckConstraint(
            "net_paise >= 0 AND taxes_paise >= 0",
            name="ck_price_snapshots_amounts_non_negative",
        ),
        sa.CheckConstraint("expires_at > fetched_at", name="ck_price_snapshots_expiry_after_fetch"),
        sa.ForeignKeyConstraint(["supplier_id"], ["suppliers.id"]),
        sa.ForeignKeyConstraint(["search_id"], ["supplier_searches.id"]),
        sa.PrimaryKeyConstraint("id", name="pk_price_snapshots"),
    )
    op.create_index("ix_price_snapshots_tenant_id", "price_snapshots", ["tenant_id"])
    op.create_index(
        "ix_price_snapshots_lookup",
        "price_snapshots",
        ["tenant_id", "supplier_id", "request_hash", "expires_at"],
    )
    op.create_index(
        "ix_price_snapshots_offer",
        "price_snapshots",
        ["tenant_id", "supplier_id", "offer_id", "fetched_at"],
    )

    op.execute(
        """
        CREATE TRIGGER price_snapshots_block_mutation
        BEFORE UPDATE OR DELETE ON price_snapshots
        FOR EACH ROW EXECUTE FUNCTION travelos_block_append_only()
        """
    )
    op.execute(
        """
        CREATE TRIGGER price_snapshots_block_truncate
        BEFORE TRUNCATE ON price_snapshots
        FOR EACH STATEMENT EXECUTE FUNCTION travelos_block_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS price_snapshots_block_truncate ON price_snapshots")
    op.execute("DROP TRIGGER IF EXISTS price_snapshots_block_mutation ON price_snapshots")
    op.drop_index("ix_price_snapshots_offer", table_name="price_snapshots")
    op.drop_index("ix_price_snapshots_lookup", table_name="price_snapshots")
    op.drop_index("ix_price_snapshots_tenant_id", table_name="price_snapshots")
    op.drop_table("price_snapshots")

    op.drop_index("ix_supplier_searches_request_hash", table_name="supplier_searches")
    op.drop_index("ix_supplier_searches_tenant_created", table_name="supplier_searches")
    op.drop_index("ix_supplier_searches_tenant_id", table_name="supplier_searches")
    op.drop_table("supplier_searches")

    op.drop_index("ix_suppliers_tenant_status", table_name="suppliers")
    op.drop_index("ix_suppliers_tenant_id", table_name="suppliers")
    op.drop_table("suppliers")
