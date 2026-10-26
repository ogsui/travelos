"""add inquiries, requirement versions and message drafts

Revision ID: 0011
Revises: 0010
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TENANT_DEFAULT = sa.text("'00000000-0000-0000-0000-000000000001'::uuid")
_CHANNELS = (
    "'whatsapp', 'email', 'sms', 'phone', 'instagram', 'facebook', 'google_business', 'web_form'"
)
_STATES = (
    "'RECEIVED', 'PARSED', 'NEEDS_INFO', 'SEARCHING', 'PRICED', 'PENDING_APPROVAL', "
    "'APPROVED', 'QUOTE_SENT', 'NEGOTIATING', 'ACCEPTED', 'AWAITING_PAYMENT', 'PAID', "
    "'BOOKING', 'BOOKED', 'INVOICED', 'DOCS_SENT', 'TRAVELLING', 'COMPLETED', 'CLOSED', "
    "'REJECTED', 'EXPIRED', 'LOST', 'CANCELLED'"
)


def _base_columns() -> list[sa.Column]:
    return [
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
    ]


def upgrade() -> None:
    op.create_table(
        "inquiries",
        *_base_columns(),
        sa.Column("customer_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("state", sa.String(length=24), server_default="RECEIVED", nullable=False),
        sa.Column("trip_type", sa.String(length=20), server_default="unknown", nullable=False),
        sa.Column("urgency", sa.String(length=10), server_default="normal", nullable=False),
        sa.Column("assigned_user_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("lost_reason", sa.String(length=255), nullable=True),
        sa.Column("duplicate_of_inquiry_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column(
            "duplicate_evidence",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.CheckConstraint(f"state IN ({_STATES})", name="ck_inquiries_state"),
        sa.CheckConstraint(
            "trip_type IN ('flight', 'package', 'hotel', 'visa', 'corporate', 'group', 'unknown')",
            name="ck_inquiries_trip_type",
        ),
        sa.CheckConstraint("urgency IN ('low', 'normal', 'high')", name="ck_inquiries_urgency"),
        sa.ForeignKeyConstraint(["customer_id"], ["customers.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["assigned_user_id"], ["users.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["duplicate_of_inquiry_id"], ["inquiries.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_inquiries"),
    )
    op.create_index("ix_inquiries_tenant_id", "inquiries", ["tenant_id"])
    op.create_index("ix_inquiries_tenant_state", "inquiries", ["tenant_id", "state"])
    op.create_index(
        "ix_inquiries_tenant_customer",
        "inquiries",
        ["tenant_id", "customer_id", "created_at"],
    )
    op.create_index("ix_inquiries_conversation", "inquiries", ["conversation_id"])
    op.create_index("ix_inquiries_duplicate_of", "inquiries", ["duplicate_of_inquiry_id"])

    op.create_table(
        "requirements",
        *_base_columns(),
        sa.Column("inquiry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column(
            "field_confidence",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "source_spans",
            postgresql.JSONB(),
            server_default=sa.text("'{}'::jsonb"),
            nullable=False,
        ),
        sa.Column(
            "missing_fields",
            postgresql.JSONB(),
            server_default=sa.text("'[]'::jsonb"),
            nullable=False,
        ),
        sa.Column("prompt_name", sa.String(length=80), nullable=True),
        sa.Column("prompt_version", sa.Integer(), nullable=True),
        sa.Column("provider", sa.String(length=80), nullable=True),
        sa.Column("model", sa.String(length=120), nullable=True),
        sa.Column("llm_call_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.CheckConstraint("version >= 1", name="ck_requirements_version_positive"),
        sa.ForeignKeyConstraint(["inquiry_id"], ["inquiries.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name="pk_requirements"),
        sa.UniqueConstraint("tenant_id", "inquiry_id", "version", name="uq_requirements_version"),
    )
    op.create_index("ix_requirements_tenant_id", "requirements", ["tenant_id"])
    op.create_index("ix_requirements_inquiry_created", "requirements", ["inquiry_id", "created_at"])

    op.create_table(
        "message_drafts",
        *_base_columns(),
        sa.Column("inquiry_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("conversation_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("contact_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("question_codes", sa.JSON(), server_default=sa.text("'[]'"), nullable=False),
        sa.Column("status", sa.String(length=20), server_default="draft", nullable=False),
        sa.Column("created_by", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(f"channel IN ({_CHANNELS})", name="ck_message_drafts_channel"),
        sa.CheckConstraint(
            "status IN ('draft', 'approved', 'rejected', 'sent')",
            name="ck_message_drafts_status",
        ),
        sa.CheckConstraint(
            "status <> 'sent' OR sent_at IS NOT NULL",
            name="ck_message_drafts_sent_needs_timestamp",
        ),
        sa.ForeignKeyConstraint(["inquiry_id"], ["inquiries.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["conversation_id"], ["conversations.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["contact_id"], ["contacts.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["approval_id"], ["approval_requests.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id", name="pk_message_drafts"),
        sa.UniqueConstraint("approval_id", name="uq_message_drafts_approval"),
    )
    op.create_index("ix_message_drafts_tenant_id", "message_drafts", ["tenant_id"])
    op.create_index(
        "ix_message_drafts_tenant_inquiry", "message_drafts", ["tenant_id", "inquiry_id"]
    )
    op.create_index("ix_message_drafts_approval", "message_drafts", ["approval_id"])


def downgrade() -> None:
    op.drop_index("ix_message_drafts_approval", table_name="message_drafts")
    op.drop_index("ix_message_drafts_tenant_inquiry", table_name="message_drafts")
    op.drop_index("ix_message_drafts_tenant_id", table_name="message_drafts")
    op.drop_table("message_drafts")
    op.drop_index("ix_requirements_inquiry_created", table_name="requirements")
    op.drop_index("ix_requirements_tenant_id", table_name="requirements")
    op.drop_table("requirements")
    op.drop_index("ix_inquiries_duplicate_of", table_name="inquiries")
    op.drop_index("ix_inquiries_conversation", table_name="inquiries")
    op.drop_index("ix_inquiries_tenant_customer", table_name="inquiries")
    op.drop_index("ix_inquiries_tenant_state", table_name="inquiries")
    op.drop_index("ix_inquiries_tenant_id", table_name="inquiries")
    op.drop_table("inquiries")
