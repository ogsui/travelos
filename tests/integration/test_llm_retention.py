"""Retention purge against PostgreSQL: cutoff, audit and the append-only guard."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import delete, select
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from travelos.core.clock import ManualClock
from travelos.core.models import AuditLog
from travelos.llm.models import LLMCall
from travelos.llm.retention import LLMCallRetentionService

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 24, 12, 0, tzinfo=UTC)
OTHER_TENANT = UUID("00000000-0000-0000-0000-0000000000aa")


def add_call(db: Session, *, created_at: datetime, tenant_id: UUID | None = None) -> LLMCall:
    row = LLMCall(
        prompt_name="retention_test",
        prompt_version=1,
        data_class="synthetic",
        status="success",
        created_at=created_at,
        **({"tenant_id": tenant_id} if tenant_id is not None else {}),
    )
    db.add(row)
    db.flush()
    return row


def test_purge_deletes_only_rows_past_the_retention_window(db: Session) -> None:
    old = add_call(db, created_at=NOW - timedelta(days=91))
    recent = add_call(db, created_at=NOW - timedelta(days=89))
    service = LLMCallRetentionService(90, clock=ManualClock(NOW))

    report = service.purge(db)

    assert report.deleted == 1
    assert report.cutoff == NOW - timedelta(days=90)
    remaining = list(db.execute(select(LLMCall)).scalars())
    assert [row.id for row in remaining] == [recent.id]
    assert old.id not in {row.id for row in remaining}

    db.flush()  # the test session has autoflush disabled
    audit = db.execute(select(AuditLog).where(AuditLog.action == "llm.calls_purged")).scalar_one()
    assert audit.after is not None
    assert audit.after["deleted"] == 1
    assert audit.after["retention_days"] == 90


def test_purge_can_be_scoped_to_one_tenant(db: Session) -> None:
    old = NOW - timedelta(days=120)
    first = add_call(db, created_at=old)
    second = add_call(db, created_at=old, tenant_id=OTHER_TENANT)
    service = LLMCallRetentionService(90, clock=ManualClock(NOW))

    report = service.purge(db, tenant_id=first.tenant_id)

    assert report.deleted == 1
    remaining = {row.id for row in db.execute(select(LLMCall)).scalars()}
    assert first.id not in remaining
    assert second.id in remaining


def test_delete_is_blocked_again_after_a_purge(db: Session) -> None:
    add_call(db, created_at=NOW - timedelta(days=100))
    add_call(db, created_at=NOW - timedelta(days=10))
    service = LLMCallRetentionService(90, clock=ManualClock(NOW))

    service.purge(db)

    # The maintenance flag is transaction-scoped, but the service also clears
    # it immediately; a later accidental delete still hits the append-only
    # guard. The recent row remains, so the row-level trigger fires.
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(delete(LLMCall).where(LLMCall.prompt_name == "retention_test"))


def test_retention_period_must_be_positive() -> None:
    with pytest.raises(ValueError, match="at least 1"):
        LLMCallRetentionService(0)
