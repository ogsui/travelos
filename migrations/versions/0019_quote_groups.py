"""add multi-option quote groups

Revision ID: 0019
Revises: 0018

A group is presentation metadata for several quotes under one inquiry. The
``quote_groups`` row carries the staff-supplied title and note; each
``quote_group_options`` row carries a staff-supplied label and an explicit
position. Nothing here ranks value/standard/premium or derives a label from a
price, because that owner rule has not been supplied.

Both tables are append-only: a title, label or membership change is a new group,
so a rendered multi-option document always describes the exact rows it was built
from. UPDATE/DELETE/TRUNCATE are blocked by ORM listeners and by the shared
``travelos_block_append_only`` trigger. A quote may belong to at most one group,
and every member quote must belong to the group's inquiry.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_DEFAULT = sa.text("'00000000-0000-0000-0000-000000000001'::uuid")


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
        "quote_groups",
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
        sa.Column("inquiry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("title", sa.String(length=200), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.CheckConstraint("length(btrim(title)) > 0", name="ck_quote_groups_title_not_blank"),
        sa.ForeignKeyConstraint(
            ["inquiry_id"],
            ["inquiries.id"],
            name="fk_quote_groups_inquiry_id_inquiries",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quote_groups"),
    )
    op.create_index("ix_quote_groups_tenant_id", "quote_groups", ["tenant_id"])
    op.create_index(
        "ix_quote_groups_tenant_inquiry",
        "quote_groups",
        ["tenant_id", "inquiry_id"],
    )

    op.create_table(
        "quote_group_options",
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
        sa.Column("group_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("quote_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("label", sa.String(length=80), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "position >= 0",
            name="ck_quote_group_options_position_non_negative",
        ),
        sa.CheckConstraint(
            "length(btrim(label)) > 0",
            name="ck_quote_group_options_label_not_blank",
        ),
        sa.ForeignKeyConstraint(
            ["group_id"],
            ["quote_groups.id"],
            name="fk_quote_group_options_group_id_quote_groups",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["quote_id"],
            ["quotes.id"],
            name="fk_quote_group_options_quote_id_quotes",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_quote_group_options"),
        sa.UniqueConstraint(
            "group_id",
            "position",
            name="uq_quote_group_options_group_id_position",
        ),
        sa.UniqueConstraint(
            "group_id",
            "label",
            name="uq_quote_group_options_group_id_label",
        ),
        sa.UniqueConstraint(
            "quote_id",
            name="uq_quote_group_options_quote_id",
        ),
    )
    op.create_index(
        "ix_quote_group_options_tenant_id",
        "quote_group_options",
        ["tenant_id"],
    )
    op.create_index(
        "ix_quote_group_options_tenant_group",
        "quote_group_options",
        ["tenant_id", "group_id"],
    )

    _append_only("quote_groups")
    _append_only("quote_group_options")


def downgrade() -> None:
    _drop_append_only("quote_group_options")
    _drop_append_only("quote_groups")

    op.drop_index("ix_quote_group_options_tenant_group", table_name="quote_group_options")
    op.drop_index("ix_quote_group_options_tenant_id", table_name="quote_group_options")
    op.drop_table("quote_group_options")

    op.drop_index("ix_quote_groups_tenant_inquiry", table_name="quote_groups")
    op.drop_index("ix_quote_groups_tenant_id", table_name="quote_groups")
    op.drop_table("quote_groups")
