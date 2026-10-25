"""encrypt customer display names

Revision ID: 0007
Revises: 0006
Create Date: 2026-10-25 15:30:00.000000

Customer display names are personal data. This migration encrypts existing rows
with the configured field-encryption key before changing the column type.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

from travelos.security.encryption import (
    ENCRYPTED_PREFIX,
    EncryptionService,
    default_key_provider,
)

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _service() -> EncryptionService:
    return EncryptionService(default_key_provider())


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("revision 0007 requires online mode to encrypt existing customer names")

    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, display_name FROM customers")).all()
    service = _service() if rows else None
    for row in rows:
        value = str(row.display_name)
        if not value.startswith(ENCRYPTED_PREFIX):
            assert service is not None
            connection.execute(
                sa.text("UPDATE customers SET display_name = :value WHERE id = :id"),
                {"id": row.id, "value": service.encrypt(value)},
            )

    op.drop_index("ix_customers_display_name", table_name="customers")
    op.alter_column(
        "customers",
        "display_name",
        existing_type=sa.String(length=200),
        type_=sa.Text(),
        existing_nullable=False,
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("revision 0007 requires online mode to decrypt customer names")

    connection = op.get_bind()
    rows = connection.execute(sa.text("SELECT id, display_name FROM customers")).all()
    service = _service() if rows else None
    for row in rows:
        value = str(row.display_name)
        if value.startswith(ENCRYPTED_PREFIX):
            assert service is not None
            connection.execute(
                sa.text("UPDATE customers SET display_name = :value WHERE id = :id"),
                {"id": row.id, "value": service.decrypt(value)},
            )

    op.alter_column(
        "customers",
        "display_name",
        existing_type=sa.Text(),
        type_=sa.String(length=200),
        existing_nullable=False,
    )
    op.create_index("ix_customers_display_name", "customers", ["display_name"], unique=False)
