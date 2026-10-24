"""Celery tasks for the outbox, timers and housekeeping.

Domain modules register an outbox handler when they start caring about an
event type. Until then the dispatcher dead-letters the event and creates a
human task: an unhandled effect is visible, never silently dropped.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy.orm import Session, sessionmaker

from travelos.core.clock import Clock
from travelos.core.config import get_settings
from travelos.core.db import session_scope
from travelos.security.sessions import AuthSessionService
from travelos.worker.celery_app import celery_app
from travelos.worker.runtime import get_session_factory
from travelos.workflow.outbox import (
    BackoffPolicy,
    DispatchReport,
    OutboxDispatcher,
    OutboxHandler,
)
from travelos.workflow.timers import TimerService

logger = logging.getLogger(__name__)

HANDLERS: dict[str, OutboxHandler] = {}


def register_handler(event_type: str, handler: OutboxHandler) -> None:
    """Register the one consumer for an event type (idempotent)."""

    if event_type in HANDLERS and HANDLERS[event_type] is not handler:
        raise ValueError(f"a handler is already registered for {event_type!r}")
    HANDLERS[event_type] = handler


def build_dispatcher(*, clock: Clock | None = None) -> OutboxDispatcher:
    settings = get_settings()
    return OutboxDispatcher(
        HANDLERS,
        clock=clock,
        backoff=BackoffPolicy(
            settings.outbox_backoff_seconds,
            settings.outbox_max_backoff_seconds,
            jitter=settings.outbox_jitter,
        ),
        max_attempts=settings.outbox_max_attempts,
        batch_size=settings.outbox_batch_size,
    )


def dispatch_pending(
    session_factory: sessionmaker[Session], *, clock: Clock | None = None
) -> DispatchReport:
    """Run one dispatch batch through an explicitly supplied session factory."""

    dispatcher = build_dispatcher(clock=clock)
    with session_scope(session_factory) as db:
        return dispatcher.dispatch(db)


@celery_app.task(name="travelos.outbox.dispatch")  # type: ignore[untyped-decorator]
def dispatch_outbox() -> dict[str, int]:
    """Deliver one batch of pending outbox events."""

    return dispatch_pending(get_session_factory()).as_dict()


@celery_app.task(name="travelos.timers.fire_due")  # type: ignore[untyped-decorator]
def fire_due_timers() -> int:
    """Fire due workflow timers and enqueue their outbox events."""

    settings = get_settings()
    service = TimerService()
    with session_scope(get_session_factory()) as db:
        fired = service.fire_due(db, limit=settings.timer_batch_size)
    if fired:
        logger.info("fired %s workflow timer(s)", fired)
    return fired


@celery_app.task(name="travelos.sessions.purge_expired")  # type: ignore[untyped-decorator]
def purge_expired_sessions() -> int:
    """Daily housekeeping: remove sessions expired or revoked over a day ago."""

    settings = get_settings()
    service = AuthSessionService(
        get_session_factory(),
        ttl=timedelta(minutes=settings.session_ttl_minutes),
    )
    with session_scope(get_session_factory()) as db:
        return service.purge_expired(db)


@celery_app.task(name="travelos.autonomy.demote")  # type: ignore[untyped-decorator]
def demote_autonomy() -> int:
    """Daily: demote actions whose metrics breach the owner-agreed rules."""

    from travelos.approval.service import AutonomyService

    with session_scope(get_session_factory()) as db:
        demotions = AutonomyService().demote(db)
    if demotions:
        logger.warning("autonomy demoted for %s action(s)", len(demotions))
    return len(demotions)


# One handler for all timer events. Approval timers are handled; a timer owned
# by another module raises and is dead-lettered until that module registers its
# own route, so no signal is silently dropped.
from travelos.approval.handlers import handle_timer_fired  # noqa: E402
from travelos.workflow.timers import TIMER_FIRED_EVENT  # noqa: E402

register_handler(TIMER_FIRED_EVENT, handle_timer_fired)
