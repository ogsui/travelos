"""add conversation handoffs and encrypted summaries

Revision ID: 0013
Revises: 0012
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0013"
down_revision: str | None = "0012"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_DEFAULT = sa.text("'00000000-0000-0000-0000-000000000001'::uuid")


def upgrade() -> None:
    op.create_table(
        "conversation_handoffs",
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
        sa.Column("inquiry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("human_task_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "triggers",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("summary", sa.Text(), nullable=False),
        sa.Column("summary_version", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.ForeignKeyConstraint(["inquiry_id"], ["inquiries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["human_task_id"], ["human_tasks.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_conversation_handoffs"),
        sa.UniqueConstraint("human_task_id", name="uq_conversation_handoffs_human_task_id"),
    )
    op.create_index("ix_conversation_handoffs_tenant_id", "conversation_handoffs", ["tenant_id"])
    op.create_index(
        "ix_conversation_handoffs_tenant_inquiry",
        "conversation_handoffs",
        ["tenant_id", "inquiry_id", "created_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_conversation_handoffs_tenant_inquiry", table_name="conversation_handoffs")
    op.drop_index("ix_conversation_handoffs_tenant_id", table_name="conversation_handoffs")
    op.drop_table("conversation_handoffs")
