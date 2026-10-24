"""workflow timers

Revision ID: 0003
Revises: 0002
Create Date: 2026-10-24 21:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "workflow_timers",
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("subject_type", sa.String(length=80), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "payload",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="scheduled",
            nullable=False,
        ),
        sa.Column("fired_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("cancelled_at", sa.DateTime(timezone=True), nullable=True),
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
            "status IN ('scheduled', 'fired', 'cancelled')",
            name=op.f("ck_workflow_timers_status"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_workflow_timers")),
        sa.UniqueConstraint(
            "tenant_id",
            "name",
            "subject_type",
            "subject_id",
            name="uq_workflow_timers_subject",
        ),
    )
    op.create_index(
        "ix_workflow_timers_status_due", "workflow_timers", ["status", "due_at"], unique=False
    )
    op.create_index(
        "ix_workflow_timers_subject",
        "workflow_timers",
        ["subject_type", "subject_id"],
        unique=False,
    )
    op.create_index(
        op.f("ix_workflow_timers_tenant_id"), "workflow_timers", ["tenant_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_workflow_timers_tenant_id"), table_name="workflow_timers")
    op.drop_index("ix_workflow_timers_subject", table_name="workflow_timers")
    op.drop_index("ix_workflow_timers_status_due", table_name="workflow_timers")
    op.drop_table("workflow_timers")
