"""llm calls metadata

Revision ID: 0005
Revises: 0004
Create Date: 2026-10-24 23:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "llm_calls",
        sa.Column("actor_id", sa.UUID(), nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=True),
        sa.Column("model", sa.String(length=160), nullable=True),
        sa.Column("prompt_name", sa.String(length=120), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("data_class", sa.String(length=20), nullable=False),
        sa.Column("status", sa.String(length=30), nullable=False),
        sa.Column("input_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("output_tokens", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("cost_paise", sa.BigInteger(), server_default=sa.text("0"), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), server_default="INR", nullable=False),
        sa.Column("latency_ms", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("cached", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("repairs", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column(
            "request",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("request_hash", sa.String(length=64), nullable=True),
        sa.Column("response_hash", sa.String(length=64), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column(
            "metadata_json",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("id", sa.UUID(), nullable=False),
        sa.Column(
            "tenant_id",
            sa.UUID(),
            server_default=sa.text("'00000000-0000-0000-0000-000000000001'::uuid"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "status IN ('success', 'schema_error', 'provider_error', "
            "'all_providers_failed', 'policy_blocked', 'budget_blocked')",
            name=op.f("ck_llm_calls_status"),
        ),
        sa.CheckConstraint(
            "data_class IN ('synthetic', 'customer', 'public')",
            name=op.f("ck_llm_calls_data_class"),
        ),
        sa.CheckConstraint(
            "prompt_version >= 1",
            name=op.f("ck_llm_calls_prompt_version_positive"),
        ),
        sa.CheckConstraint(
            "input_tokens >= 0 AND output_tokens >= 0",
            name=op.f("ck_llm_calls_tokens_non_negative"),
        ),
        sa.CheckConstraint("cost_paise >= 0", name=op.f("ck_llm_calls_cost_non_negative")),
        sa.CheckConstraint("latency_ms >= 0", name=op.f("ck_llm_calls_latency_non_negative")),
        sa.CheckConstraint("repairs >= 0", name=op.f("ck_llm_calls_repairs_non_negative")),
        sa.ForeignKeyConstraint(
            ["actor_id"],
            ["users.id"],
            name=op.f("fk_llm_calls_actor_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_llm_calls")),
    )
    op.create_index(
        "ix_llm_calls_prompt",
        "llm_calls",
        ["tenant_id", "prompt_name", "created_at"],
        unique=False,
    )
    op.create_index("ix_llm_calls_status", "llm_calls", ["status", "created_at"], unique=False)
    op.create_index("ix_llm_calls_provider", "llm_calls", ["provider", "created_at"], unique=False)
    op.create_index(op.f("ix_llm_calls_tenant_id"), "llm_calls", ["tenant_id"], unique=False)

    # The shared trigger function predates this table and names audit_log in
    # its message. Replace it with the table name so every append-only table
    # reports accurately. The behaviour is unchanged.
    op.execute(
        """
        CREATE OR REPLACE FUNCTION travelos_block_append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION
                '% is append-only: % is forbidden', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER llm_calls_block_mutation
        BEFORE UPDATE OR DELETE ON llm_calls
        FOR EACH ROW EXECUTE FUNCTION travelos_block_append_only()
        """
    )
    op.execute(
        """
        CREATE TRIGGER llm_calls_block_truncate
        BEFORE TRUNCATE ON llm_calls
        FOR EACH STATEMENT EXECUTE FUNCTION travelos_block_append_only()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER IF EXISTS llm_calls_block_truncate ON llm_calls")
    op.execute("DROP TRIGGER IF EXISTS llm_calls_block_mutation ON llm_calls")
    op.drop_index(op.f("ix_llm_calls_tenant_id"), table_name="llm_calls")
    op.drop_index("ix_llm_calls_provider", table_name="llm_calls")
    op.drop_index("ix_llm_calls_status", table_name="llm_calls")
    op.drop_index("ix_llm_calls_prompt", table_name="llm_calls")
    op.drop_table("llm_calls")
