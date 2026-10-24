"""Transactional outbox: at-least-once delivery with idempotent consumers.

Producers write an event in the same transaction as the state change they are
reacting to. A worker then claims pending rows with ``FOR UPDATE SKIP LOCKED``,
invokes exactly one handler, and either marks the event ``done``, schedules a
jittered retry, or dead-letters it. Dead letters create a ``human_tasks`` row,
so nothing is silently dropped.

A handler is wrapped in an idempotency claim keyed by the event id. If the same
event is delivered twice (worker crash after the side effect but before the
commit, or a broker redelivery), the second claim finds the existing row inside
the same transaction and the handler is not called again.
"""

from __future__ import annotations

import logging
import random
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from travelos.core.audit import jsonable
from travelos.core.clock import Clock, SystemClock
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.ids import uuid7
from travelos.core.models import HumanTask, IdempotencyKey, OutboxEvent

logger = logging.getLogger(__name__)

OutboxHandler = Callable[[Session, OutboxEvent], None]
DEAD_LETTER_SCOPE = "outbox:dead_letter"
_IDEMPOTENCY_SCOPE = "outbox"
_MAX_TITLE = 200


@dataclass(slots=True)
class DispatchReport:
    """Result of one dispatch batch; safe to return from a Celery task."""

    processed: int = 0
    succeeded: int = 0
    failed: int = 0
    dead: int = 0
    skipped: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "processed": self.processed,
            "succeeded": self.succeeded,
            "failed": self.failed,
            "dead": self.dead,
            "skipped": self.skipped,
        }


class OutboxWriter:
    """Adds outbox rows to the caller's transaction."""

    def __init__(self, clock: Clock | None = None) -> None:
        self._clock = clock or SystemClock()

    def enqueue(
        self,
        db: Session,
        *,
        event_type: str,
        payload: Mapping[str, Any] | None = None,
        available_at: datetime | None = None,
        tenant_id: UUID | None = None,
    ) -> OutboxEvent:
        if not event_type or len(event_type) > 120:
            raise ValueError("event_type must be 1..120 characters")
        when = available_at or self._clock.now()
        if when.tzinfo is None:
            raise ValueError("available_at must be timezone-aware")
        event = OutboxEvent(
            tenant_id=tenant_id or DEFAULT_TENANT_ID,
            event_type=event_type,
            payload=jsonable(dict(payload or {})),
            status="pending",
            attempts=0,
            available_at=when,
        )
        db.add(event)
        db.flush()
        return event


class BackoffPolicy:
    """Exponential backoff with bounded, symmetric jitter."""

    def __init__(
        self,
        base_seconds: float,
        max_seconds: float,
        *,
        jitter: float = 0.2,
        random_source: Callable[[], float] | None = None,
    ) -> None:
        if base_seconds <= 0:
            raise ValueError("base_seconds must be positive")
        if max_seconds < base_seconds:
            raise ValueError("max_seconds must be >= base_seconds")
        if not 0.0 <= jitter <= 1.0:
            raise ValueError("jitter must be between 0 and 1")
        self._base = base_seconds
        self._max = max_seconds
        self._jitter = jitter
        self._random = random_source or random.random

    def delay(self, attempt: int) -> timedelta:
        """Delay before the next try, where ``attempt`` is 1 for the first failure."""

        if attempt < 1:
            raise ValueError("attempt must be >= 1")
        raw = min(self._base * float(2 ** min(attempt - 1, 20)), self._max)
        sampled = min(max(self._random(), 0.0), 1.0)
        factor = 1.0 + self._jitter * (2.0 * sampled - 1.0)
        return timedelta(seconds=max(0.0, raw * factor))


class IdempotentConsumer:
    """Claims an event id once per scope inside the current transaction."""

    def __init__(self, scope: str = _IDEMPOTENCY_SCOPE) -> None:
        if not scope:
            raise ValueError("scope must not be empty")
        self._scope = scope

    def claim(self, db: Session, event: OutboxEvent, *, scope: str | None = None) -> bool:
        """Return True when this caller won the claim and should run the handler."""

        statement = (
            pg_insert(IdempotencyKey)
            .values(
                id=uuid7(),
                tenant_id=event.tenant_id,
                key=str(event.id),
                scope=scope or self._scope,
            )
            .on_conflict_do_nothing(index_elements=["key", "scope"])
            .returning(IdempotencyKey.id)
        )
        return db.execute(statement).scalar_one_or_none() is not None


class OutboxDispatcher:
    """Claims and processes pending events; the caller owns the transaction."""

    def __init__(
        self,
        handlers: Mapping[str, OutboxHandler],
        *,
        clock: Clock | None = None,
        consumer: IdempotentConsumer | None = None,
        backoff: BackoffPolicy | None = None,
        max_attempts: int = 5,
        batch_size: int = 25,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        self._handlers = dict(handlers)
        self._clock = clock or SystemClock()
        self._consumer = consumer or IdempotentConsumer()
        self._backoff = backoff or BackoffPolicy(10.0, 3600.0)
        self._max_attempts = max_attempts
        self._batch_size = batch_size

    def dispatch(self, db: Session, *, limit: int | None = None) -> DispatchReport:
        """Process up to ``limit`` due events. Locks are held until the caller commits."""

        batch = self._batch_size if limit is None else max(1, limit)
        statement = (
            select(OutboxEvent)
            .where(
                OutboxEvent.status == "pending",
                OutboxEvent.available_at <= self._clock.now(),
            )
            .order_by(OutboxEvent.available_at, OutboxEvent.id)
            .limit(batch)
            .with_for_update(skip_locked=True)
        )
        events = list(db.execute(statement).scalars())
        report = DispatchReport()
        for event in events:
            self._deliver(db, event, report)
            # Persist the status change before the next claim: a second
            # dispatch in the same transaction must not re-select this row.
            db.flush()
        return report

    def _deliver(self, db: Session, event: OutboxEvent, report: DispatchReport) -> None:
        report.processed += 1
        handler = self._handlers.get(event.event_type)
        if handler is None:
            self._fail(db, event, f"no handler registered for {event.event_type!r}", permanent=True)
            report.dead += 1
            return

        try:
            with db.begin_nested():
                if not self._consumer.claim(db, event):
                    event.status = "done"
                    event.last_error = None
                    report.skipped += 1
                    return
                handler(db, event)
        except Exception as exc:  # handler failures must not abort the whole batch
            logger.warning(
                "outbox handler failed",
                extra={"event_id": str(event.id), "event_type": event.event_type},
                exc_info=exc,
            )
            self._fail(db, event, f"{type(exc).__name__}: {exc}")
            report.failed += 1
            if event.status == "dead":
                report.dead += 1
        else:
            event.status = "done"
            event.last_error = None
            report.succeeded += 1

    def _fail(
        self,
        db: Session,
        event: OutboxEvent,
        error: str,
        *,
        permanent: bool = False,
    ) -> None:
        event.attempts += 1
        event.last_error = error[:2000]
        if permanent or event.attempts >= self._max_attempts:
            event.status = "dead"
            self._dead_letter(db, event)
            return
        event.status = "pending"
        event.available_at = self._clock.now() + self._backoff.delay(event.attempts)

    def _dead_letter(self, db: Session, event: OutboxEvent) -> None:
        if not self._consumer.claim(db, event, scope=DEAD_LETTER_SCOPE):
            return
        title = (f"Outbox event {event.event_type} failed after {event.attempts} attempt(s)")[
            :_MAX_TITLE
        ]
        db.add(
            HumanTask(
                tenant_id=event.tenant_id,
                task_type="outbox.dead_letter",
                subject_type="outbox_event",
                subject_id=event.id,
                title=title,
                assignee_role="admin",
                priority="high",
            )
        )
