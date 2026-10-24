"""approval requests

Revision ID: 0004
Revises: 0003
Create Date: 2026-10-24 22:15:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "approval_requests",
        sa.Column("action_type", sa.String(length=120), nullable=False),
        sa.Column("subject_type", sa.String(length=80), nullable=False),
        sa.Column("subject_id", sa.UUID(), nullable=True),
        sa.Column("requested_by", sa.UUID(), nullable=True),
        sa.Column("status", sa.String(length=20), server_default="pending", nullable=False),
        sa.Column("level", sa.String(length=2), nullable=False),
        sa.Column(
            "proposed",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column("final", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.Column(
            "context",
            postgresql.JSONB(astext_type=sa.Text()),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "requires_four_eyes",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("approvals_needed", sa.Integer(), server_default=sa.text("1"), nullable=False),
        sa.Column("approvals_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("reviewer_id", sa.UUID(), nullable=True),
        sa.Column("second_reviewer_id", sa.UUID(), nullable=True),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("escalation_count", sa.Integer(), server_default=sa.text("0"), nullable=False),
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
            "status IN ('pending', 'approved', 'edited', 'rejected', 'expired')",
            name=op.f("ck_approval_requests_status"),
        ),
        sa.CheckConstraint(
            "level IN ('A0', 'A1', 'A2', 'A3')",
            name=op.f("ck_approval_requests_level"),
        ),
        sa.CheckConstraint(
            "approvals_needed IN (1, 2)",
            name=op.f("ck_approval_requests_approvals_needed"),
        ),
        sa.CheckConstraint(
            "approvals_count >= 0 AND approvals_count <= approvals_needed",
            name=op.f("ck_approval_requests_approvals_count"),
        ),
        sa.CheckConstraint(
            "status <> 'approved' OR approvals_count = approvals_needed",
            name=op.f("ck_approval_requests_approved_needs_all_approvals"),
        ),
        sa.CheckConstraint(
            "escalation_count >= 0",
            name=op.f("ck_approval_requests_escalation_non_negative"),
        ),
        sa.ForeignKeyConstraint(
            ["requested_by"],
            ["users.id"],
            name=op.f("fk_approval_requests_requested_by_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["reviewer_id"],
            ["users.id"],
            name=op.f("fk_approval_requests_reviewer_id_users"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["second_reviewer_id"],
            ["users.id"],
            name=op.f("fk_approval_requests_second_reviewer_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_approval_requests")),
    )
    op.create_index(
        "ix_approval_requests_status",
        "approval_requests",
        ["tenant_id", "status", "created_at"],
        unique=False,
    )
    op.create_index(
        "ix_approval_requests_action",
        "approval_requests",
        ["tenant_id", "action_type", "status"],
        unique=False,
    )
    op.create_index(
        "ix_approval_requests_subject",
        "approval_requests",
        ["subject_type", "subject_id"],
        unique=False,
    )
    op.create_index(
        "ix_approval_requests_expires",
        "approval_requests",
        ["status", "expires_at"],
        unique=False,
    )
    op.create_index(
        op.f("ix_approval_requests_tenant_id"),
        "approval_requests",
        ["tenant_id"],
        unique=False,
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_approval_requests_tenant_id"), table_name="approval_requests")
    op.drop_index("ix_approval_requests_expires", table_name="approval_requests")
    op.drop_index("ix_approval_requests_subject", table_name="approval_requests")
    op.drop_index("ix_approval_requests_action", table_name="approval_requests")
    op.drop_index("ix_approval_requests_status", table_name="approval_requests")
    op.drop_table("approval_requests")
