"""add owner price overrides

Revision ID: 0021
Revises: 0020

``quote_price_overrides`` records the owner's decision on a ``below_floor`` or
``uncompetitive`` price. The matrix row stays LOCKED A0 (a human decides), so the
decision is not an ``approval_requests`` row. Each override cites the source
calculation, the deterministic calculation that carries the owner's total, the
``human_tasks`` decision it completed, the reviewer and the reason.

``quote_revisions.pricing_override_id`` is the link a customer-facing revision
uses to prove its non-``ok`` price was authorised. Both the new table and the new
column are append-only in practice: the table has the shared
``travelos_block_append_only`` trigger, and a revision never changes after insert.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0021"
down_revision: str | None = "0020"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_DEFAULT = sa.text("'00000000-0000-0000-0000-000000000001'::uuid")
_STATUSES = "'below_floor', 'uncompetitive'"
_CURRENCY = "'^[A-Z]{3}$'"


def _append_only(table: str) -> None:
    op.execute(
        f"""
        CREATE TRIGGER {table}_block_mutation
        BEFORE UPDATE OR DELETE ON {table}
        FOR EACH ROW EXECUTE FUNCTION travelos_block_append_only()
        """
    )
    op.execute(
        f"""
        CREATE TRIGGER {table}_block_truncate
        BEFORE TRUNCATE ON {table}
        FOR EACH STATEMENT EXECUTE FUNCTION travelos_block_append_only()
        """
    )


def _drop_append_only(table: str) -> None:
    op.execute(f"DROP TRIGGER IF EXISTS {table}_block_truncate ON {table}")
    op.execute(f"DROP TRIGGER IF EXISTS {table}_block_mutation ON {table}")


def upgrade() -> None:
    op.create_table(
        "quote_price_overrides",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            server_default=_TENANT_DEFAULT,
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("source_calculation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "authorised_calculation_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("decision_task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("floor_paise", sa.BigInteger(), nullable=False),
        sa.Column("target_paise", sa.BigInteger(), nullable=False),
        sa.Column("ceiling_paise", sa.BigInteger(), nullable=False),
        sa.Column("source_customer_price_paise", sa.BigInteger(), nullable=True),
        sa.Column("owner_total_paise", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.CheckConstraint(
            f"status IN ({_STATUSES})",
            name="ck_quote_price_overrides_status",
        ),
        sa.CheckConstraint(
            "length(btrim(reason)) > 0",
            name="ck_quote_price_overrides_reason_not_blank",
        ),
        sa.CheckConstraint(
            "floor_paise >= 0",
            name="ck_quote_price_overrides_floor_non_negative",
        ),
        sa.CheckConstraint(
            "target_paise >= 0",
            name="ck_quote_price_overrides_target_non_negative",
        ),
        sa.CheckConstraint(
            "ceiling_paise >= 0",
            name="ck_quote_price_overrides_ceiling_non_negative",
        ),
        sa.CheckConstraint(
            "source_customer_price_paise IS NULL OR source_customer_price_paise >= 0",
            name="ck_quote_price_overrides_source_customer_price_non_negative",
        ),
        sa.CheckConstraint(
            "owner_total_paise >= 0",
            name="ck_quote_price_overrides_owner_total_non_negative",
        ),
        sa.CheckConstraint(
            f"currency ~ {_CURRENCY}",
            name="ck_quote_price_overrides_currency_code",
        ),
        sa.ForeignKeyConstraint(
            ["source_calculation_id"],
            ["price_calculations.id"],
            name="fk_quote_price_overrides_source_calculation_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["authorised_calculation_id"],
            ["price_calculations.id"],
            name="fk_quote_price_overrides_authorised_calculation_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["decision_task_id"],
            ["human_tasks.id"],
            name="fk_quote_price_overrides_decision_task_id",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["owner_user_id"],
            ["users.id"],
            name="fk_quote_price_overrides_owner_user_id",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quote_price_overrides"),
        sa.UniqueConstraint(
            "tenant_id",
            "authorised_calculation_id",
            name="uq_quote_price_overrides_tenant_authorised_calculation_id",
        ),
    )
    op.create_index(
        "ix_quote_price_overrides_tenant_id",
        "quote_price_overrides",
        ["tenant_id"],
    )
    op.create_index(
        "ix_quote_price_overrides_tenant_source",
        "quote_price_overrides",
        ["tenant_id", "source_calculation_id"],
    )
    _append_only("quote_price_overrides")

    op.add_column(
        "quote_revisions",
        sa.Column("pricing_override_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_foreign_key(
        "fk_quote_revisions_pricing_override_id",
        "quote_revisions",
        "quote_price_overrides",
        ["pricing_override_id"],
        ["id"],
        ondelete="RESTRICT",
    )
    op.create_unique_constraint(
        "uq_quote_revisions_tenant_pricing_override_id",
        "quote_revisions",
        ["tenant_id", "pricing_override_id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "uq_quote_revisions_tenant_pricing_override_id",
        "quote_revisions",
        type_="unique",
    )
    op.drop_constraint(
        "fk_quote_revisions_pricing_override_id",
        "quote_revisions",
        type_="foreignkey",
    )
    op.drop_column("quote_revisions", "pricing_override_id")

    _drop_append_only("quote_price_overrides")
    op.drop_index(
        "ix_quote_price_overrides_tenant_source",
        table_name="quote_price_overrides",
    )
    op.drop_index(
        "ix_quote_price_overrides_tenant_id",
        table_name="quote_price_overrides",
    )
    op.drop_table("quote_price_overrides")
