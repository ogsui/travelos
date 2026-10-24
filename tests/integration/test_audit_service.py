"""AuditWriter persistence, diffing, redaction and transaction semantics."""

from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from travelos.core.audit import Actor, AuditWriter
from travelos.core.models import AuditLog
from travelos.core.redaction import REDACTED

pytestmark = pytest.mark.integration


def test_record_persists_redacted_payloads(db: Session) -> None:
    writer = AuditWriter()
    actor_id = uuid4()
    subject_id = uuid4()

    writer.record(
        db,
        action="test.record",
        actor=Actor.user(actor_id),
        subject_type="user",
        subject_id=subject_id,
        before={"email": "owner@example.test", "role": "agent", "phone": "+919000000123"},
        after={"email": "owner@example.test", "role": "owner", "phone": "+919000000123"},
        ip="127.0.0.1",
    )
    db.flush()

    row = db.execute(select(AuditLog)).scalar_one()
    assert row.actor_type == "user"
    assert row.actor_id == actor_id
    assert row.action == "test.record"
    assert row.subject_type == "user"
    assert row.subject_id == subject_id
    assert row.ip == "127.0.0.1"
    assert row.before == {"email": REDACTED, "role": "agent", "phone": REDACTED}
    assert row.after == {"email": REDACTED, "role": "owner", "phone": REDACTED}


def test_record_change_stores_only_changed_fields(db: Session) -> None:
    writer = AuditWriter()

    writer.record_change(
        db,
        action="flags.updated",
        actor=Actor.system(),
        subject_type="feature_flag",
        subject_id=uuid4(),
        before={"enabled": False, "scope": "global"},
        after={"enabled": True, "scope": "global"},
    )
    db.flush()

    row = db.execute(select(AuditLog)).scalar_one()
    assert row.before == {"enabled": False}
    assert row.after == {"enabled": True}


def test_audit_rows_roll_back_with_the_callers_transaction(db: Session) -> None:
    writer = AuditWriter()
    writer.record(db, action="test.rollback", actor=Actor.system())
    db.flush()

    db.rollback()

    assert db.execute(select(func.count()).select_from(AuditLog)).scalar_one() == 0


def test_system_actions_need_no_actor_id(db: Session) -> None:
    writer = AuditWriter()
    writer.record(db, action="test.system", actor=Actor.system())
    db.flush()

    row = db.execute(select(AuditLog)).scalar_one()
    assert row.actor_type == "system"
    assert row.actor_id is None
