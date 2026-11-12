"""add append-only quote events

Revision ID: 0020
Revises: 0019

``quote_events`` is the customer-facing timeline of one quote: sent, viewed,
accepted, rejected or expired. Each row copies the revision number and total the
customer saw at that moment, so the timeline can be read without joining the
revision and still states exactly what was accepted.

The table is append-only: UPDATE/DELETE/TRUNCATE are blocked by ORM listeners
and by the shared ``travelos_block_append_only`` trigger. The quote lifecycle row
(``quotes.state``) is the mutable pointer; the events are history.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_DEFAULT = sa.text("'00000000-0000-0000-0000-000000000001'::uuid")
_EVENT_KINDS = "'sent', 'viewed', 'accepted', 'rejected', 'expired'"
_EVENT_ACTORS = "'customer', 'staff', 'system'"


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
        "quote_events",
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
        sa.Column("quote_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("total_paise", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), nullable=False),
        sa.Column(
            "actor_type",
            sa.String(length=20),
            server_default="system",
            nullable=False,
        ),
        sa.CheckConstraint(
            "position >= 0",
            name="ck_quote_events_position_non_negative",
        ),
        sa.CheckConstraint(
            "revision_number >= 1",
            name="ck_quote_events_revision_number_positive",
        ),
        sa.CheckConstraint(f"kind IN ({_EVENT_KINDS})", name="ck_quote_events_kind"),
        sa.CheckConstraint(
            "total_paise >= 0",
            name="ck_quote_events_total_non_negative",
        ),
        sa.CheckConstraint(
            "currency ~ '^[A-Z]{3}$'",
            name="ck_quote_events_currency_code",
        ),
        sa.CheckConstraint(
            f"actor_type IN ({_EVENT_ACTORS})",
            name="ck_quote_events_actor_type",
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"],
            ["quotes.id"],
            name="fk_quote_events_quote_id_quotes",
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quote_events"),
        sa.UniqueConstraint(
            "quote_id",
            "position",
            name="uq_quote_events_quote_id_position",
        ),
    )
    op.create_index("ix_quote_events_tenant_id", "quote_events", ["tenant_id"])
    op.create_index(
        "ix_quote_events_tenant_quote",
        "quote_events",
        ["tenant_id", "quote_id", "position"],
    )
    op.create_index(
        "ix_quote_events_tenant_created",
        "quote_events",
        ["tenant_id", "created_at"],
    )

    _append_only("quote_events")


def downgrade() -> None:
    _drop_append_only("quote_events")

    op.drop_index("ix_quote_events_tenant_created", table_name="quote_events")
    op.drop_index("ix_quote_events_tenant_quote", table_name="quote_events")
    op.drop_index("ix_quote_events_tenant_id", table_name="quote_events")
    op.drop_table("quote_events")
