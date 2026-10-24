"""Persistent workflow timers driven by an injectable clock.

Timers cover first-response SLAs, quote expiry, payment reminders and booking
hold expiry. Scheduling is an upsert: re-arming a reminder replaces the due
time instead of creating duplicates. Firing is one-shot and writes a
``timer.fired`` outbox event plus an audit row in the same transaction, so a
worker crash cannot lose the signal and a duplicate beat run cannot fire twice.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from travelos.core.audit import Actor, AuditWriter, jsonable
from travelos.core.clock import Clock, SystemClock
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.ids import uuid7
from travelos.workflow.models import WorkflowTimer
from travelos.workflow.outbox import OutboxWriter

logger = logging.getLogger(__name__)

TIMER_FIRED_EVENT = "timer.fired"


class TimerKind(StrEnum):
    """Timer names with an owner-agreed default delay."""

    INQUIRY_SLA = "inquiry.sla"
    INQUIRY_FOLLOW_UP = "inquiry.follow_up"
    QUOTE_EXPIRY = "quote.expiry"
    PAYMENT_REMINDER = "payment.reminder"
    BOOKING_HOLD_EXPIRY = "booking.hold_expiry"


DEFAULT_TIMER_DELAYS: Mapping[TimerKind, timedelta] = {
    TimerKind.INQUIRY_SLA: timedelta(minutes=15),
    TimerKind.INQUIRY_FOLLOW_UP: timedelta(hours=24),
    TimerKind.QUOTE_EXPIRY: timedelta(days=7),
    TimerKind.PAYMENT_REMINDER: timedelta(hours=24),
    TimerKind.BOOKING_HOLD_EXPIRY: timedelta(minutes=30),
}


def default_delay(name: str) -> timedelta | None:
    """Return the configured delay for a known timer name, else ``None``."""

    try:
        kind = TimerKind(name)
    except ValueError:
        return None
    return DEFAULT_TIMER_DELAYS[kind]


class TimerService:
    """Schedules, re-arms, cancels and fires persistent timers."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        outbox: OutboxWriter | None = None,
        audit: AuditWriter | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._outbox = outbox or OutboxWriter(self._clock)
        self._audit = audit or AuditWriter()

    def schedule(
        self,
        db: Session,
        *,
        name: str,
        subject_type: str,
        subject_id: UUID,
        delay: timedelta | None = None,
        at: datetime | None = None,
        payload: Mapping[str, Any] | None = None,
        tenant_id: UUID | None = None,
        actor: Actor | None = None,
    ) -> WorkflowTimer:
        """Create or re-arm the one timer for this (tenant, name, subject)."""

        if delay is not None and at is not None:
            raise ValueError("pass either delay or at, not both")
        if at is None:
            effective = delay if delay is not None else default_delay(name)
            if effective is None:
                raise ValueError(f"no default delay for timer {name!r}; pass delay or at")
            if effective < timedelta(0):
                raise ValueError("timer delay must not be negative")
            due_at = self._clock.now() + effective
        else:
            due_at = at
        if due_at.tzinfo is None:
            raise ValueError("timer due time must be timezone-aware")

        safe_payload = jsonable(dict(payload or {}))
        tenant = tenant_id or DEFAULT_TENANT_ID
        statement = (
            pg_insert(WorkflowTimer)
            .values(
                id=uuid7(),
                tenant_id=tenant,
                name=name,
                subject_type=subject_type,
                subject_id=subject_id,
                due_at=due_at,
                payload=safe_payload,
                status="scheduled",
            )
            .on_conflict_do_update(
                constraint="uq_workflow_timers_subject",
                set_={
                    "due_at": due_at,
                    "payload": safe_payload,
                    "status": "scheduled",
                    "fired_at": None,
                    "cancelled_at": None,
                },
            )
            .returning(WorkflowTimer.id)
        )
        timer_id = db.execute(statement).scalar_one()
        timer = db.get(WorkflowTimer, timer_id, populate_existing=True)
        if timer is None:  # pragma: no cover - returning() guarantees the row
            raise RuntimeError("workflow timer disappeared during upsert")
        self._audit.record(
            db,
            action="workflow.timer_scheduled",
            actor=actor or Actor.system(),
            subject_type=subject_type,
            subject_id=subject_id,
            after={
                "timer": name,
                "due_at": due_at,
                "timer_id": timer.id,
                "payload": safe_payload,
            },
        )
        return timer

    def cancel(
        self,
        db: Session,
        *,
        name: str,
        subject_type: str,
        subject_id: UUID,
        actor: Actor | None = None,
    ) -> bool:
        """Cancel a scheduled timer. Fired or already-cancelled timers return False."""

        timer = db.execute(
            select(WorkflowTimer)
            .where(
                WorkflowTimer.name == name,
                WorkflowTimer.subject_type == subject_type,
                WorkflowTimer.subject_id == subject_id,
                WorkflowTimer.status == "scheduled",
            )
            .with_for_update()
        ).scalar_one_or_none()
        if timer is None:
            return False
        now = self._clock.now()
        timer.status = "cancelled"
        timer.cancelled_at = now
        self._audit.record(
            db,
            action="workflow.timer_cancelled",
            actor=actor or Actor.system(),
            subject_type=subject_type,
            subject_id=subject_id,
            before={"timer": name, "due_at": timer.due_at},
            after={"timer": name, "cancelled_at": now},
        )
        db.flush()
        return True

    def fire_due(self, db: Session, *, limit: int = 100) -> int:
        """Mark due timers fired and enqueue one ``timer.fired`` event each."""

        if limit < 1:
            raise ValueError("limit must be >= 1")
        now = self._clock.now()
        timers = list(
            db.execute(
                select(WorkflowTimer)
                .where(
                    WorkflowTimer.status == "scheduled",
                    WorkflowTimer.due_at <= now,
                )
                .order_by(WorkflowTimer.due_at, WorkflowTimer.id)
                .limit(limit)
                .with_for_update(skip_locked=True)
            )
            .scalars()
            .all()
        )

        for timer in timers:
            timer.status = "fired"
            timer.fired_at = now
            self._outbox.enqueue(
                db,
                event_type=TIMER_FIRED_EVENT,
                tenant_id=timer.tenant_id,
                payload={
                    "timer_id": timer.id,
                    "name": timer.name,
                    "subject_type": timer.subject_type,
                    "subject_id": timer.subject_id,
                    "due_at": timer.due_at,
                    "fired_at": now,
                    "payload": timer.payload,
                },
            )
            self._audit.record(
                db,
                action="workflow.timer_fired",
                actor=Actor.system(),
                subject_type=timer.subject_type,
                subject_id=timer.subject_id,
                after={"timer": timer.name, "fired_at": now, "timer_id": timer.id},
            )
        return len(timers)
