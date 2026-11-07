"""add pricing rule sets, tax tables, FX rates and price calculations

Revision ID: 0017
Revises: 0016

All four tables are append-only: a correction is a newer version or a newer
observation, never an edit, so a stored quote keeps the rules, tax table and
exchange rate it actually used. The PostgreSQL triggers reuse
``travelos_block_append_only``, so UPDATE, DELETE and TRUNCATE fail even from raw
SQL; ORM listeners raise ``AppendOnlyError`` first.

Prices are integer paise and FX rates are exact rationals (``numerator`` /
``denominator``), so no float or numeric column is introduced. ``price_calculations``
uses ``quote_revision_id`` without a foreign key until task 016 creates
``quote_revisions``; a calculation for a preview may have no quote revision at all.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_DEFAULT = sa.text("'00000000-0000-0000-0000-000000000001'::uuid")
_CURRENCY_PATTERN = "^[A-Z]{3}$"
_SOURCE_HASH_PATTERN = "^[0-9a-f]{64}$"
_PRICE_STATUSES = "'ok', 'below_floor', 'uncompetitive', 'needs_config'"


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
        "pricing_rule_sets",
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
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("rules", postgresql.JSONB(), nullable=False),
        sa.Column("source_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active_from", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_pricing_rule_sets_version_positive")),
        sa.CheckConstraint(
            f"source_hash ~ '{_SOURCE_HASH_PATTERN}'",
            name=op.f("ck_pricing_rule_sets_source_hash_hex"),
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"], ["users.id"], name="fk_pricing_rule_sets_approved_by_users"
        ),
        sa.UniqueConstraint("tenant_id", "version", name="uq_pricing_rule_sets_version"),
        sa.PrimaryKeyConstraint("id", name="pk_pricing_rule_sets"),
    )
    op.create_index("ix_pricing_rule_sets_tenant_id", "pricing_rule_sets", ["tenant_id"])
    op.create_index(
        "ix_pricing_rule_sets_active", "pricing_rule_sets", ["tenant_id", "active_from"]
    )

    op.create_table(
        "tax_rule_tables",
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
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("rows", postgresql.JSONB(), nullable=False),
        sa.Column("source_hash", sa.CHAR(length=64), nullable=False),
        sa.Column("approved_by_accountant", sa.Boolean(), nullable=False),
        sa.Column("approved_on", sa.Date(), nullable=False),
        sa.Column("approved_by", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("active_from", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint("version >= 1", name=op.f("ck_tax_rule_tables_version_positive")),
        sa.CheckConstraint(
            f"source_hash ~ '{_SOURCE_HASH_PATTERN}'",
            name=op.f("ck_tax_rule_tables_source_hash_hex"),
        ),
        sa.ForeignKeyConstraint(
            ["approved_by"], ["users.id"], name="fk_tax_rule_tables_approved_by_users"
        ),
        sa.UniqueConstraint("tenant_id", "version", name="uq_tax_rule_tables_version"),
        sa.PrimaryKeyConstraint("id", name="pk_tax_rule_tables"),
    )
    op.create_index("ix_tax_rule_tables_tenant_id", "tax_rule_tables", ["tenant_id"])
    op.create_index("ix_tax_rule_tables_active", "tax_rule_tables", ["tenant_id", "active_from"])

    op.create_table(
        "fx_rates",
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
        sa.Column("base_currency", sa.CHAR(length=3), nullable=False),
        sa.Column("quote_currency", sa.CHAR(length=3), nullable=False),
        sa.Column("numerator", sa.BigInteger(), nullable=False),
        sa.Column("denominator", sa.BigInteger(), nullable=False),
        sa.Column("source", sa.String(length=200), nullable=False),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            f"base_currency ~ '{_CURRENCY_PATTERN}'", name=op.f("ck_fx_rates_base_currency_code")
        ),
        sa.CheckConstraint(
            f"quote_currency ~ '{_CURRENCY_PATTERN}'", name=op.f("ck_fx_rates_quote_currency_code")
        ),
        sa.CheckConstraint("numerator > 0", name=op.f("ck_fx_rates_numerator_positive")),
        sa.CheckConstraint("denominator > 0", name=op.f("ck_fx_rates_denominator_positive")),
        sa.PrimaryKeyConstraint("id", name="pk_fx_rates"),
    )
    op.create_index("ix_fx_rates_tenant_id", "fx_rates", ["tenant_id"])
    op.create_index(
        "ix_fx_rates_pair",
        "fx_rates",
        ["tenant_id", "base_currency", "quote_currency", "fetched_at"],
    )

    op.create_table(
        "price_calculations",
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
        sa.Column("quote_revision_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("rule_set_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("tax_table_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column("rule_version", sa.Integer(), nullable=False),
        sa.Column("tax_table_version", sa.Integer(), nullable=False),
        sa.Column("floor_paise", sa.BigInteger(), nullable=True),
        sa.Column("target_paise", sa.BigInteger(), nullable=True),
        sa.Column("ceiling_paise", sa.BigInteger(), nullable=True),
        sa.Column("recommended_paise", sa.BigInteger(), nullable=True),
        sa.Column("customer_price_paise", sa.BigInteger(), nullable=True),
        sa.Column("inputs", postgresql.JSONB(), nullable=False),
        sa.Column("outputs", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            f"status IN ({_PRICE_STATUSES})", name=op.f("ck_price_calculations_status")
        ),
        sa.CheckConstraint(
            f"currency ~ '{_CURRENCY_PATTERN}'", name=op.f("ck_price_calculations_currency_code")
        ),
        sa.CheckConstraint(
            "rule_version >= 1", name=op.f("ck_price_calculations_rule_version_positive")
        ),
        sa.CheckConstraint(
            "tax_table_version >= 1", name=op.f("ck_price_calculations_tax_table_version_positive")
        ),
        sa.CheckConstraint(
            "floor_paise IS NULL OR floor_paise >= 0",
            name=op.f("ck_price_calculations_floor_non_negative"),
        ),
        sa.CheckConstraint(
            "target_paise IS NULL OR target_paise >= 0",
            name=op.f("ck_price_calculations_target_non_negative"),
        ),
        sa.CheckConstraint(
            "ceiling_paise IS NULL OR ceiling_paise >= 0",
            name=op.f("ck_price_calculations_ceiling_non_negative"),
        ),
        sa.CheckConstraint(
            "recommended_paise IS NULL OR recommended_paise >= 0",
            name=op.f("ck_price_calculations_recommended_non_negative"),
        ),
        sa.CheckConstraint(
            "customer_price_paise IS NULL OR customer_price_paise >= 0",
            name=op.f("ck_price_calculations_customer_price_non_negative"),
        ),
        sa.CheckConstraint(
            "(status IN ('ok', 'below_floor')) = (customer_price_paise IS NOT NULL)",
            name=op.f("ck_price_calculations_customer_price_matches_status"),
        ),
        sa.CheckConstraint(
            "(status IN ('ok', 'below_floor')) = (recommended_paise IS NOT NULL)",
            name=op.f("ck_price_calculations_recommended_matches_status"),
        ),
        sa.CheckConstraint(
            "(status = 'needs_config') = (floor_paise IS NULL)",
            name=op.f("ck_price_calculations_levels_missing_for_needs_config"),
        ),
        sa.CheckConstraint(
            "(status <> 'needs_config') = "
            "(floor_paise IS NOT NULL AND target_paise IS NOT NULL AND ceiling_paise IS NOT NULL)",
            name=op.f("ck_price_calculations_levels_present_when_priced"),
        ),
        sa.ForeignKeyConstraint(
            ["rule_set_id"],
            ["pricing_rule_sets.id"],
            name="fk_price_calculations_rule_set_id_pricing_rule_sets",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["tax_table_id"],
            ["tax_rule_tables.id"],
            name="fk_price_calculations_tax_table_id_tax_rule_tables",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_price_calculations"),
    )
    op.create_index("ix_price_calculations_tenant_id", "price_calculations", ["tenant_id"])
    op.create_index(
        "ix_price_calculations_created", "price_calculations", ["tenant_id", "created_at"]
    )
    op.create_index(
        "ix_price_calculations_rule_set", "price_calculations", ["tenant_id", "rule_set_id"]
    )
    op.create_index(
        "ix_price_calculations_tax_table", "price_calculations", ["tenant_id", "tax_table_id"]
    )
    op.create_index(
        "ix_price_calculations_quote_revision", "price_calculations", ["quote_revision_id"]
    )

    for table in ("pricing_rule_sets", "tax_rule_tables", "fx_rates", "price_calculations"):
        _append_only(table)


def downgrade() -> None:
    for table in ("price_calculations", "fx_rates", "tax_rule_tables", "pricing_rule_sets"):
        _drop_append_only(table)

    op.drop_index("ix_price_calculations_quote_revision", table_name="price_calculations")
    op.drop_index("ix_price_calculations_tax_table", table_name="price_calculations")
    op.drop_index("ix_price_calculations_rule_set", table_name="price_calculations")
    op.drop_index("ix_price_calculations_created", table_name="price_calculations")
    op.drop_index("ix_price_calculations_tenant_id", table_name="price_calculations")
    op.drop_table("price_calculations")

    op.drop_index("ix_fx_rates_pair", table_name="fx_rates")
    op.drop_index("ix_fx_rates_tenant_id", table_name="fx_rates")
    op.drop_table("fx_rates")

    op.drop_index("ix_tax_rule_tables_active", table_name="tax_rule_tables")
    op.drop_index("ix_tax_rule_tables_tenant_id", table_name="tax_rule_tables")
    op.drop_table("tax_rule_tables")

    op.drop_index("ix_pricing_rule_sets_active", table_name="pricing_rule_sets")
    op.drop_index("ix_pricing_rule_sets_tenant_id", table_name="pricing_rule_sets")
    op.drop_table("pricing_rule_sets")
