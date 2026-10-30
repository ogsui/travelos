"""add immutable competitor price snapshots

Revision ID: 0016
Revises: 0015

``competitor_snapshots`` is append-only: the PostgreSQL triggers reuse
``travelos_block_append_only``, so an UPDATE, DELETE or TRUNCATE fails even from
raw SQL. A newer price is a newer row. An ``ok`` row carries an integer price and
currency; a blocked or unavailable run carries neither, so "no price" is stored
explicitly instead of being indistinguishable from "never asked".
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_DEFAULT = sa.text("'00000000-0000-0000-0000-000000000001'::uuid")


def upgrade() -> None:
    op.create_table(
        "competitor_snapshots",
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
        sa.Column("site", sa.String(length=120), nullable=False),
        sa.Column("query_hash", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("price_paise", sa.BigInteger(), nullable=True),
        sa.Column("currency", sa.CHAR(length=3), nullable=True),
        sa.Column("comparability_score", sa.Integer(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("detail", sa.String(length=500), nullable=True),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("basis", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "status IN ('ok', 'blocked', 'unavailable')",
            name="ck_competitor_snapshots_status",
        ),
        sa.CheckConstraint(
            "(status = 'ok') = (price_paise IS NOT NULL)",
            name="ck_competitor_snapshots_price_matches_status",
        ),
        sa.CheckConstraint(
            "(price_paise IS NULL) = (currency IS NULL)",
            name="ck_competitor_snapshots_currency_matches_price",
        ),
        sa.CheckConstraint(
            "price_paise IS NULL OR price_paise >= 0",
            name="ck_competitor_snapshots_price_non_negative",
        ),
        sa.CheckConstraint(
            "comparability_score IS NULL "
            "OR (comparability_score >= 0 AND comparability_score <= 100)",
            name="ck_competitor_snapshots_score_range",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_competitor_snapshots"),
    )
    op.create_index("ix_competitor_snapshots_tenant_id", "competitor_snapshots", ["tenant_id"])
    op.create_index(
        "ix_competitor_snapshots_query",
        "competitor_snapshots",
        ["tenant_id", "query_hash", "fetched_at"],
    )
    op.create_index(
        "ix_competitor_snapshots_site",
        "competitor_snapshots",
        ["tenant_id", "site", "fetched_at"],
    )

    op.execute(
        """
        CREATE TRIGGER competitor_snapshots_block_mutation
        BEFORE UPDATE OR DELETE ON competitor_snapshots
        FOR EACH ROW EXECUTE FUNCTION travelos_block_append_only()
        """
    )
    op.execute(
        """
        CREATE TRIGGER competitor_snapshots_block_truncate
        BEFORE TRUNCATE ON competitor_snapshots
        FOR EACH STATEMENT EXECUTE FUNCTION travelos_block_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS competitor_snapshots_block_truncate ON competitor_snapshots")
    op.execute("DROP TRIGGER IF EXISTS competitor_snapshots_block_mutation ON competitor_snapshots")
    op.drop_index("ix_competitor_snapshots_site", table_name="competitor_snapshots")
    op.drop_index("ix_competitor_snapshots_query", table_name="competitor_snapshots")
    op.drop_index("ix_competitor_snapshots_tenant_id", table_name="competitor_snapshots")
    op.drop_table("competitor_snapshots")
