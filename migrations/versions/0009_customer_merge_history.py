"""track merged customers and reversible CRM merge state

Revision ID: 0009
Revises: 0008
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "customers",
        sa.Column("merged_into_customer_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.add_column("customers", sa.Column("merged_at", sa.DateTime(timezone=True), nullable=True))
    op.create_foreign_key(
        "fk_customers_merged_into_customer_id_customers",
        "customers",
        "customers",
        ["merged_into_customer_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(
        "ix_customers_merged_into_customer_id", "customers", ["merged_into_customer_id"]
    )
    op.create_table(
        "customer_merge_history",
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "tenant_id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("'00000000-0000-0000-0000-000000000001'::uuid"),
            nullable=False,
        ),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("approval_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("target_customer_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("contacts_before", sa.JSON(), nullable=False),
        sa.Column("contacts_after", sa.JSON(), nullable=False),
        sa.Column("traveller_ids", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=20), server_default="applied", nullable=False),
        sa.Column(
            "applied_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("undone_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "state IN ('applied', 'undone')", name="ck_customer_merge_history_state"
        ),
        sa.ForeignKeyConstraint(["approval_id"], ["approval_requests.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["source_customer_id"], ["customers.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["target_customer_id"], ["customers.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id", name="pk_customer_merge_history"),
        sa.UniqueConstraint("approval_id", name="uq_customer_merge_history_approval_id"),
    )
    op.create_index(
        "ix_customer_merge_history_tenant_id_source_customer_id",
        "customer_merge_history",
        ["tenant_id", "source_customer_id"],
    )
    op.create_index(
        "ix_customer_merge_history_tenant_id_target_customer_id",
        "customer_merge_history",
        ["tenant_id", "target_customer_id"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_customer_merge_history_tenant_id_target_customer_id",
        table_name="customer_merge_history",
    )
    op.drop_index(
        "ix_customer_merge_history_tenant_id_source_customer_id",
        table_name="customer_merge_history",
    )
    op.drop_table("customer_merge_history")
    op.drop_index("ix_customers_merged_into_customer_id", table_name="customers")
    op.drop_constraint(
        "fk_customers_merged_into_customer_id_customers", "customers", type_="foreignkey"
    )
    op.drop_column("customers", "merged_at")
    op.drop_column("customers", "merged_into_customer_id")
