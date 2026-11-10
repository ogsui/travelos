"""add quotes, quote revisions and quote lines

Revision ID: 0018
Revises: 0017

A quote is one option under an inquiry. ``quotes`` carries the mutable lifecycle
(state, current revision, expiry, accept-token hash); ``quote_revisions`` and
``quote_lines`` are append-only so a customer-facing price can always be traced
back to the calculation and lines shown. The same
``travelos_block_append_only`` trigger used elsewhere blocks UPDATE, DELETE and
TRUNCATE even from raw SQL.

``price_calculations.quote_revision_id`` stays a plain nullable UUID, not a
foreign key. The authoritative link for quote creation is
``quote_revisions.price_calculation_id``: a revision must cite the calculation
that produced its total. The reverse pointer is only a hint recorded when a
caller already knows the revision id, and may be null for a pricing preview. A
deferrable foreign key was considered and rejected: it queues deferred RI
triggers, which makes ``TRUNCATE price_calculations`` fail with "pending trigger
events" before the append-only trigger runs, and it adds no guarantee the
authoritative direction does not already provide. A calculation that deliberately
names a future revision id can still be recorded before that revision exists.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_DEFAULT = sa.text("'00000000-0000-0000-0000-000000000001'::uuid")
_STATES = (
    "'DRAFT', 'PENDING_APPROVAL', 'APPROVED', 'SENT', 'VIEWED', 'ACCEPTED', "
    "'EXPIRED', 'REVISED', 'REJECTED'"
)
_LINE_KINDS = "'supplier', 'margin', 'tax', 'gateway', 'rounding', 'proposal'"
_CURRENCY_PATTERN = "^[A-Z]{3}$"
_TOKEN_PATTERN = "^[0-9a-f]{64}$"


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
        "quotes",
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
        sa.Column("current_revision", sa.Integer(), server_default="0", nullable=False),
        sa.Column("state", sa.String(length=20), server_default="DRAFT", nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("accept_token_hash", sa.CHAR(length=64), nullable=True),
        sa.CheckConstraint("current_revision >= 0", name="ck_quotes_current_revision_non_negative"),
        sa.CheckConstraint(f"state IN ({_STATES})", name="ck_quotes_state"),
        sa.CheckConstraint(
            f"accept_token_hash IS NULL OR accept_token_hash ~ '{_TOKEN_PATTERN}'",
            name="ck_quotes_accept_token_hash_hex",
        ),
        sa.ForeignKeyConstraint(
            ["inquiry_id"],
            ["inquiries.id"],
            name="fk_quotes_inquiry_id_inquiries",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quotes"),
        sa.UniqueConstraint(
            "tenant_id", "accept_token_hash", name="uq_quotes_tenant_accept_token_hash"
        ),
    )
    op.create_index("ix_quotes_tenant_id", "quotes", ["tenant_id"])
    op.create_index("ix_quotes_tenant_state", "quotes", ["tenant_id", "state"])
    op.create_index("ix_quotes_tenant_inquiry", "quotes", ["tenant_id", "inquiry_id"])
    op.create_index("ix_quotes_tenant_expires", "quotes", ["tenant_id", "expires_at"])

    op.create_table(
        "quote_revisions",
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
        sa.Column("quote_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("number", sa.Integer(), nullable=False),
        sa.Column("itinerary_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("price_calculation_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("total_paise", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), server_default="INR", nullable=False),
        sa.Column("pdf_ref", sa.String(length=500), nullable=True),
        sa.Column("diff_from_previous", postgresql.JSONB(), nullable=True),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("number >= 1", name="ck_quote_revisions_number_positive"),
        sa.CheckConstraint("total_paise >= 0", name="ck_quote_revisions_total_non_negative"),
        sa.CheckConstraint(
            f"currency ~ '{_CURRENCY_PATTERN}'", name="ck_quote_revisions_currency_code"
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"],
            ["quotes.id"],
            name="fk_quote_revisions_quote_id_quotes",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["itinerary_id"],
            ["itineraries.id"],
            name="fk_quote_revisions_itinerary_id_itineraries",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["price_calculation_id"],
            ["price_calculations.id"],
            name="fk_quote_revisions_price_calculation_id_price_calculations",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["approval_id"],
            ["approval_requests.id"],
            name="fk_quote_revisions_approval_id_approval_requests",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quote_revisions"),
        sa.UniqueConstraint("quote_id", "number", name="uq_quote_revisions_quote_id_number"),
        sa.UniqueConstraint(
            "tenant_id",
            "price_calculation_id",
            name="uq_quote_revisions_tenant_price_calculation_id",
        ),
    )
    op.create_index("ix_quote_revisions_tenant_id", "quote_revisions", ["tenant_id"])
    op.create_index(
        "ix_quote_revisions_tenant_quote",
        "quote_revisions",
        ["tenant_id", "quote_id"],
    )
    op.create_index(
        "ix_quote_revisions_calculation",
        "quote_revisions",
        ["price_calculation_id"],
    )

    op.create_table(
        "quote_lines",
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
        sa.Column("revision_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("description", sa.Text(), nullable=False),
        sa.Column("snapshot_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("amount_paise", sa.BigInteger(), nullable=False),
        sa.Column("currency", sa.CHAR(length=3), server_default="INR", nullable=False),
        sa.CheckConstraint("position >= 0", name="ck_quote_lines_position_non_negative"),
        sa.CheckConstraint(f"kind IN ({_LINE_KINDS})", name="ck_quote_lines_kind"),
        sa.CheckConstraint(
            f"currency ~ '{_CURRENCY_PATTERN}'", name="ck_quote_lines_currency_code"
        ),
        sa.ForeignKeyConstraint(
            ["revision_id"],
            ["quote_revisions.id"],
            name="fk_quote_lines_revision_id_quote_revisions",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["price_snapshots.id"],
            name="fk_quote_lines_snapshot_id_price_snapshots",
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quote_lines"),
        sa.UniqueConstraint("revision_id", "position", name="uq_quote_lines_revision_id_position"),
    )
    op.create_index("ix_quote_lines_tenant_id", "quote_lines", ["tenant_id"])
    op.create_index("ix_quote_lines_revision", "quote_lines", ["revision_id"])
    op.create_index("ix_quote_lines_snapshot", "quote_lines", ["snapshot_id"])

    _append_only("quote_revisions")
    _append_only("quote_lines")


def downgrade() -> None:
    _drop_append_only("quote_lines")
    _drop_append_only("quote_revisions")

    op.drop_index("ix_quote_lines_snapshot", table_name="quote_lines")
    op.drop_index("ix_quote_lines_revision", table_name="quote_lines")
    op.drop_index("ix_quote_lines_tenant_id", table_name="quote_lines")
    op.drop_table("quote_lines")

    op.drop_index("ix_quote_revisions_calculation", table_name="quote_revisions")
    op.drop_index("ix_quote_revisions_tenant_quote", table_name="quote_revisions")
    op.drop_index("ix_quote_revisions_tenant_id", table_name="quote_revisions")
    op.drop_table("quote_revisions")

    op.drop_index("ix_quotes_tenant_expires", table_name="quotes")
    op.drop_index("ix_quotes_tenant_inquiry", table_name="quotes")
    op.drop_index("ix_quotes_tenant_state", table_name="quotes")
    op.drop_index("ix_quotes_tenant_id", table_name="quotes")
    op.drop_table("quotes")
