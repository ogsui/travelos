"""Retention purge for ``llm_calls``.

The table is append-only for normal code. The owner set a 90-day retention
period for redacted call metadata, so this module owns the one deliberate
deletion path:

1. it uses a raw ``DELETE`` (never an ORM delete);
2. it opens a transaction-scoped PostgreSQL maintenance flag that only the
   trigger function on ``llm_calls`` honours, and closes it immediately;
3. it writes one audit row with counts and the cutoff, never prompt content.

A nightly worker task calls it. Each tenant's rows age out independently; the
cutoff is global because retention is a data-minimisation policy, not business
data.
"""

from __future__ import annotations

import dataclasses
from datetime import datetime, timedelta
from typing import Any, cast
from uuid import UUID

from sqlalchemy import delete, text
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from travelos.core.audit import Actor, AuditWriter
from travelos.core.clock import Clock, SystemClock
from travelos.llm.models import LLMCall

# The trigger on llm_calls allows DELETE only while this GUC is exactly 'on'.
_PURGE_FLAG = "travelos.llm_retention_purge"
_SET_FLAG = text(f"SET LOCAL {_PURGE_FLAG} = 'on'")
_CLEAR_FLAG = text(f"SET LOCAL {_PURGE_FLAG} = 'off'")


@dataclasses.dataclass(frozen=True, slots=True)
class PurgeReport:
    """What one purge run removed. Safe to log and audit."""

    deleted: int
    cutoff: datetime
    retention_days: int


class LLMCallRetentionService:
    """Delete redacted LLM call metadata older than the retention period."""

    def __init__(
        self,
        retention_days: int = 90,
        *,
        clock: Clock | None = None,
        audit: AuditWriter | None = None,
    ) -> None:
        if retention_days < 1:
            raise ValueError("retention_days must be at least 1")
        self._retention_days = retention_days
        self._clock = clock or SystemClock()
        self._audit = audit or AuditWriter()

    @property
    def retention_days(self) -> int:
        return self._retention_days

    def purge(self, db: Session, *, tenant_id: UUID | None = None) -> PurgeReport:
        """Delete rows older than the retention cutoff.

        The caller owns the transaction: the audit row commits with the delete
        or rolls back with it.
        """

        now = self._clock.now()
        cutoff = now - timedelta(days=self._retention_days)
        statement = delete(LLMCall).where(LLMCall.created_at < cutoff)
        if tenant_id is not None:
            statement = statement.where(LLMCall.tenant_id == tenant_id)

        db.execute(_SET_FLAG)
        try:
            result = cast(CursorResult[Any], db.execute(statement))
        finally:
            db.execute(_CLEAR_FLAG)

        deleted = int(result.rowcount or 0)
        if deleted:
            self._audit.record(
                db,
                action="llm.calls_purged",
                actor=Actor.system(),
                subject_type="llm_call",
                after={
                    "deleted": deleted,
                    "cutoff": cutoff.isoformat(),
                    "retention_days": self._retention_days,
                    "tenant_id": str(tenant_id) if tenant_id is not None else None,
                },
            )
        return PurgeReport(deleted=deleted, cutoff=cutoff, retention_days=self._retention_days)
