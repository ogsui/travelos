"""replace legacy contact SHA-256 hashes with keyed fingerprints

Revision ID: 0008
Revises: 0007
"""

from __future__ import annotations

import hashlib
from collections import defaultdict
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import context, op

from travelos.crm.fingerprints import ContactFingerprinter
from travelos.security.encryption import (
    ENCRYPTED_PREFIX,
    EncryptionService,
    default_key_provider,
)

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def _services() -> tuple[EncryptionService, ContactFingerprinter]:
    provider = default_key_provider()
    return EncryptionService(provider), ContactFingerprinter(provider)


def _read_contacts(connection: sa.Connection):
    rows = connection.execute(
        sa.text("SELECT id, channel, value, value_hash FROM contacts ORDER BY id")
    ).all()
    if not rows:
        return rows, None, None
    encryption, fingerprinter = _services()
    return rows, encryption, fingerprinter


def upgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("revision 0008 requires online mode to re-key contact fingerprints")

    connection = op.get_bind()
    rows, encryption, fingerprinter = _read_contacts(connection)
    if not rows:
        return

    legacy_to_keyed: dict[tuple[str, str], str] = {}
    keyed_to_legacy: dict[tuple[str, str], str] = {}
    updates: list[dict[str, object]] = []
    for row in rows:
        stored_value = str(row.value)
        value = (
            encryption.decrypt(stored_value)
            if stored_value.startswith(ENCRYPTED_PREFIX)
            else stored_value
        )
        old_hash = row.value_hash
        keyed_hash = fingerprinter.fingerprint(str(row.channel), value)
        if old_hash is not None:
            legacy_to_keyed[(str(row.channel), str(old_hash))] = keyed_hash
        legacy_to_keyed[(str(row.channel), keyed_hash)] = keyed_hash
        keyed_to_legacy.setdefault(
            (str(row.channel), keyed_hash), hashlib.sha256(value.encode("utf-8")).hexdigest()
        )
        updates.append({"id": row.id, "value_hash": keyed_hash})

    suppression_rows = connection.execute(
        sa.text("SELECT id, channel, value_hash FROM suppression_list ORDER BY id")
    ).all()
    grouped: dict[tuple[str, str], list[object]] = defaultdict(list)
    for row in suppression_rows:
        channel = str(row.channel)
        digest = legacy_to_keyed.get((channel, str(row.value_hash)))
        if digest is None:
            raise RuntimeError(
                "cannot re-key a suppression entry without a matching contact; "
                "resolve this row through an authorized data migration before retrying"
            )
        grouped[(channel, digest)].append(row.id)

    # Remove rows that collapse to one normalized contact before restoring the
    # existing unique(channel, value_hash) constraint.
    for (_channel, digest), ids in grouped.items():
        if len(ids) > 1:
            for duplicate_id in ids[1:]:
                connection.execute(
                    sa.text("DELETE FROM suppression_list WHERE id = :id"),
                    {"id": duplicate_id},
                )
        connection.execute(
            sa.text("UPDATE suppression_list SET value_hash = :digest WHERE id = :id"),
            {"id": ids[0], "digest": digest},
        )
    connection.execute(
        sa.text("UPDATE contacts SET value_hash = :value_hash WHERE id = :id"), updates
    )


def downgrade() -> None:
    if context.is_offline_mode():
        raise RuntimeError("revision 0008 requires online mode to restore legacy contact hashes")

    connection = op.get_bind()
    rows, encryption, fingerprinter = _read_contacts(connection)
    if not rows:
        return
    keyed_to_legacy: dict[tuple[str, str], str] = {}
    updates: list[dict[str, object]] = []
    for row in rows:
        stored_value = str(row.value)
        value = (
            encryption.decrypt(stored_value)
            if stored_value.startswith(ENCRYPTED_PREFIX)
            else stored_value
        )
        keyed_hash = fingerprinter.fingerprint(str(row.channel), value)
        old_hash = hashlib.sha256(value.encode("utf-8")).hexdigest()
        keyed_to_legacy.setdefault((str(row.channel), keyed_hash), old_hash)
        updates.append({"id": row.id, "value_hash": old_hash})

    for row in connection.execute(
        sa.text("SELECT id, channel, value_hash FROM suppression_list")
    ).all():
        old_hash = keyed_to_legacy.get((str(row.channel), str(row.value_hash)))
        if old_hash is None:
            raise RuntimeError("cannot restore a suppression digest without a matching contact")
        connection.execute(
            sa.text("UPDATE suppression_list SET value_hash = :digest WHERE id = :id"),
            {"id": row.id, "digest": old_hash},
        )
    connection.execute(
        sa.text("UPDATE contacts SET value_hash = :value_hash WHERE id = :id"), updates
    )
