"""llm calls retention purge

Revision ID: 0006
Revises: 0005
Create Date: 2026-10-24 23:55:00.000000

The owner set a 90-day retention period for redacted ``llm_calls`` metadata.
Deletion stays impossible for normal code: the shared append-only trigger now
allows DELETE only while a transaction-scoped maintenance flag is exactly
``on``. ``LLMCallRetentionService`` sets and clears it around one statement.

This is not a permission boundary against a compromised application process
(which could set the same flag); it is a guard against accidental deletes,
ad-hoc SQL and an ORM bug. Add a dedicated database role if stronger separation
is needed later.
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_BLOCK_ALL = """
CREATE OR REPLACE FUNCTION travelos_block_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION
        '% is append-only: % is forbidden', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""

_ALLOW_RETENTION_PURGE = """
CREATE OR REPLACE FUNCTION travelos_block_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
    IF TG_TABLE_NAME = 'llm_calls'
       AND TG_OP = 'DELETE'
       AND current_setting('travelos.llm_retention_purge', true) = 'on' THEN
        RETURN OLD;
    END IF;
    RAISE EXCEPTION
        '% is append-only: % is forbidden', TG_TABLE_NAME, TG_OP
        USING ERRCODE = 'insufficient_privilege';
END;
$$
"""


def upgrade() -> None:
    op.create_index("ix_llm_calls_created_at", "llm_calls", ["created_at"], unique=False)
    op.create_index(
        "ix_llm_calls_tenant_created_at",
        "llm_calls",
        ["tenant_id", "created_at"],
        unique=False,
    )
    op.execute(_ALLOW_RETENTION_PURGE)


def downgrade() -> None:
    op.execute(_BLOCK_ALL)
    op.drop_index("ix_llm_calls_tenant_created_at", table_name="llm_calls")
    op.drop_index("ix_llm_calls_created_at", table_name="llm_calls")
