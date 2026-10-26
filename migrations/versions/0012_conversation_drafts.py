"""add draft kind and template key for sales replies

Revision ID: 0012
Revises: 0011
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "message_drafts",
        sa.Column(
            "kind",
            sa.String(length=20),
            server_default="clarification",
            nullable=False,
        ),
    )
    op.add_column(
        "message_drafts",
        sa.Column("template_key", sa.String(length=80), nullable=True),
    )
    op.create_check_constraint(
        "ck_message_drafts_kind",
        "message_drafts",
        "kind IN ('clarification', 'acknowledgement', 'sales_reply')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_message_drafts_kind", "message_drafts", type_="check")
    op.drop_column("message_drafts", "template_key")
    op.drop_column("message_drafts", "kind")
