"""Workflow engine integration tests: audit, outbox, retries, DLQ and timers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.core.audit import Actor
from travelos.core.clock import ManualClock
from travelos.core.models import AuditLog, HumanTask, OutboxEvent
from travelos.workflow.errors import GuardRejectedError, IllegalTransitionError
from travelos.workflow.models import WorkflowTimer
from travelos.workflow.outbox import (
    BackoffPolicy,
    OutboxDispatcher,
    OutboxWriter,
)
from travelos.workflow.service import WorkflowService
from travelos.workflow.timers import TIMER_FIRED_EVENT, TimerKind, TimerService

pytestmark = pytest.mark.integration

SUBJECT = UUID("00000000-0000-0000-0000-0000000000aa")


def audit_actions(db: Session) -> list[str]:
    return list(db.execute(select(AuditLog.action).order_by(AuditLog.at)).scalars())


# --------------------------------------------------------------------------
# Transition service
# --------------------------------------------------------------------------


def test_apply_records_audit_in_the_caller_transaction(db: Session) -> None:
    service = WorkflowService()
    actor = Actor.user(uuid4())

    result = service.apply(
        db,
        machine="quote",
        subject_type="quote",
        subject_id=SUBJECT,
        state="DRAFT",
        trigger="request_approval",
        actor=actor,
    )
    db.flush()

    assert result.target == "PENDING_APPROVAL"
    rows = list(db.execute(select(AuditLog)).scalars())
    assert len(rows) == 1
    entry = rows[0]
    assert entry.action == "workflow.transition"
    assert entry.actor_type == "user"
    assert entry.actor_id == actor.id
    assert entry.subject_type == "quote"
    assert entry.subject_id == SUBJECT
    assert entry.before == {"state": "DRAFT"}
    assert entry.after == {
        "state": "PENDING_APPROVAL",
        "trigger": "request_approval",
        "machine": "quote",
    }


def test_apply_enqueues_one_event_per_declared_effect(db: Session) -> None:
    service = WorkflowService()

    result = service.apply(
        db,
        machine="quote",
        subject_type="quote",
        subject_id=SUBJECT,
        state="APPROVED",
        trigger="send",
        payload={"quote_id": str(SUBJECT)},
    )
    db.flush()

    assert result.effects == ("quote.sent",)
    events = list(db.execute(select(OutboxEvent)).scalars())
    assert len(events) == 1
    event = events[0]
    assert event.event_type == "quote.sent"
    assert event.status == "pending"
    assert event.attempts == 0
    assert event.payload == {
        "quote_id": str(SUBJECT),
        "machine": "quote",
        "source": "APPROVED",
        "target": "SENT",
        "trigger": "send",
        "subject_type": "quote",
        "subject_id": str(SUBJECT),
    }


def test_apply_does_not_let_caller_payload_override_transition_facts(
    db: Session,
) -> None:
    service = WorkflowService()
    service.apply(
        db,
        machine="content_item",
        subject_type="content_item",
        subject_id=SUBJECT,
        state="SCHEDULED",
        trigger="publish",
        payload={"target": "HACKED", "machine": "other"},
    )
    db.flush()

    event = db.execute(select(OutboxEvent)).scalar_one()
    assert event.payload["target"] == "PUBLISHED"
    assert event.payload["machine"] == "content_item"


def test_rejected_transition_writes_nothing(db: Session) -> None:
    service = WorkflowService()

    with pytest.raises(IllegalTransitionError):
        service.apply(
            db,
            machine="quote",
            subject_type="quote",
            subject_id=SUBJECT,
            state="DRAFT",
            trigger="send",
        )
    with pytest.raises(GuardRejectedError):
        service.apply(
            db,
            machine="booking",
            subject_type="booking",
            subject_id=SUBJECT,
            state="PENDING_CONFIRMATION",
            trigger="confirm",
        )
    db.flush()

    assert list(db.execute(select(AuditLog)).scalars()) == []
    assert list(db.execute(select(OutboxEvent)).scalars()) == []


def test_confirmed_booking_emits_its_effect(db: Session) -> None:
    service = WorkflowService()
    result = service.apply(
        db,
        machine="booking",
        subject_type="booking",
        subject_id=SUBJECT,
        state="PENDING_CONFIRMATION",
        trigger="confirm",
        context={"human_confirmed": True},
    )
    assert result.target == "REVALIDATING"
    assert result.effects == ()


# --------------------------------------------------------------------------
# Outbox dispatch
# --------------------------------------------------------------------------


def _dispatcher(
    handlers,
    *,
    clock: ManualClock | None = None,
    max_attempts: int = 5,
    batch_size: int = 25,
) -> OutboxDispatcher:
    return OutboxDispatcher(
        handlers,
        clock=clock,
        backoff=BackoffPolicy(60, 60, jitter=0.0, random_source=lambda: 0.5),
        max_attempts=max_attempts,
        batch_size=batch_size,
    )


def test_dispatch_calls_the_handler_and_marks_the_event_done(db: Session) -> None:
    writer = OutboxWriter()
    event = writer.enqueue(db, event_type="test.handled", payload={"n": 1})
    seen: list[UUID] = []

    def handler(session: Session, row: OutboxEvent) -> None:
        assert session is db
        seen.append(row.id)

    report = _dispatcher({"test.handled": handler}).dispatch(db)

    assert report.as_dict() == {
        "processed": 1,
        "succeeded": 1,
        "failed": 0,
        "dead": 0,
        "skipped": 0,
    }
    assert seen == [event.id]
    assert event.status == "done"
    assert event.attempts == 0
    assert event.last_error is None


def test_duplicate_delivery_does_not_run_side_effects_twice(db: Session) -> None:
    writer = OutboxWriter()
    event = writer.enqueue(db, event_type="test.duplicate")
    calls: list[UUID] = []

    def handler(_session: Session, row: OutboxEvent) -> None:
        calls.append(row.id)

    dispatcher = _dispatcher({"test.duplicate": handler})
    dispatcher.dispatch(db)
    assert calls == [event.id]

    # Simulate a broker redelivery of the same event id.
    event.status = "pending"
    db.flush()
    report = dispatcher.dispatch(db)

    assert calls == [event.id]
    assert report.skipped == 1
    assert event.status == "done"


def test_failed_handler_retries_with_backoff_then_dead_letters(db: Session) -> None:
    clock = ManualClock(datetime(2026, 3, 1, 9, 0, tzinfo=UTC))
    writer = OutboxWriter(clock)
    event = writer.enqueue(db, event_type="test.always_fails")
    attempts: list[int] = []

    def handler(_session: Session, _row: OutboxEvent) -> None:
        attempts.append(len(attempts) + 1)
        raise RuntimeError("synthetic supplier timeout")

    dispatcher = _dispatcher({"test.always_fails": handler}, clock=clock, max_attempts=3)

    first = dispatcher.dispatch(db)
    assert first.failed == 1
    assert event.status == "pending"
    assert event.attempts == 1
    assert "synthetic supplier timeout" in (event.last_error or "")
    assert event.available_at == clock.now() + timedelta(seconds=60)

    # Not due yet: the retry is not attempted early.
    assert dispatcher.dispatch(db).processed == 0
    assert len(attempts) == 1

    clock.advance(timedelta(seconds=60))
    dispatcher.dispatch(db)
    assert event.attempts == 2
    assert event.status == "pending"

    clock.advance(timedelta(seconds=60))
    final = dispatcher.dispatch(db)
    assert final.dead == 1
    assert event.status == "dead"
    assert event.attempts == 3
    assert len(attempts) == 3

    tasks = list(db.execute(select(HumanTask)).scalars())
    assert len(tasks) == 1
    assert tasks[0].task_type == "outbox.dead_letter"
    assert tasks[0].subject_type == "outbox_event"
    assert tasks[0].subject_id == event.id
    assert tasks[0].priority == "high"
    assert tasks[0].status == "open"
    assert tasks[0].assignee_role == "admin"


def test_unhandled_event_type_is_dead_lettered_immediately(db: Session) -> None:
    writer = OutboxWriter()
    event = writer.enqueue(db, event_type="nobody.listens")

    report = _dispatcher({}).dispatch(db)

    assert report.dead == 1
    assert event.status == "dead"
    assert event.attempts == 1
    task = db.execute(select(HumanTask)).scalar_one()
    assert task.subject_id == event.id


def test_dispatch_respects_the_batch_limit(db: Session) -> None:
    writer = OutboxWriter()
    writer.enqueue(db, event_type="test.one")
    writer.enqueue(db, event_type="test.one")
    seen: list[UUID] = []

    def handler(_session: Session, row: OutboxEvent) -> None:
        seen.append(row.id)

    dispatcher = _dispatcher({"test.one": handler}, batch_size=1)
    assert dispatcher.dispatch(db).processed == 1
    assert dispatcher.dispatch(db).processed == 1
    assert dispatcher.dispatch(db).processed == 0
    assert len(seen) == 2


def test_handler_can_enqueue_a_follow_up_event_in_the_same_transaction(
    db: Session,
) -> None:
    writer = OutboxWriter()
    parent = writer.enqueue(db, event_type="test.parent")

    def handler(session: Session, row: OutboxEvent) -> None:
        writer.enqueue(session, event_type="test.child", payload={"parent": str(row.id)})

    dispatcher = _dispatcher({"test.parent": handler, "test.child": lambda *_: None})
    dispatcher.dispatch(db)
    assert parent.status == "done"

    follow_up = db.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == "test.child")
    ).scalar_one()
    assert follow_up.payload == {"parent": str(parent.id)}
    assert dispatcher.dispatch(db).succeeded == 1


def test_worker_dispatch_pending_commits_and_is_idempotent(db_factory) -> None:
    """Exercise the real Celery entry point's function without a broker."""

    from travelos.worker import tasks

    seen: list[UUID] = []

    def handler(_session: Session, event: OutboxEvent) -> None:
        seen.append(event.id)

    tasks.register_handler("test.worker.path", handler)
    writer = OutboxWriter()
    with db_factory() as db:
        event = writer.enqueue(db, event_type="test.worker.path")
        event_id = event.id
        db.commit()

    report = tasks.dispatch_pending(db_factory, clock=ManualClock())
    assert report.succeeded == 1
    assert seen == [event_id]

    with db_factory() as db:
        stored = db.get(OutboxEvent, event_id)
        assert stored is not None
        assert stored.status == "done"


# --------------------------------------------------------------------------
# Timers
# --------------------------------------------------------------------------


def _timer_service(clock: ManualClock) -> TimerService:
    return TimerService(clock=clock, outbox=OutboxWriter(clock))


def test_timer_schedule_uses_the_default_delay(db: Session) -> None:
    clock = ManualClock(datetime(2026, 4, 1, 8, 0, tzinfo=UTC))
    service = _timer_service(clock)

    timer = service.schedule(
        db,
        name=TimerKind.QUOTE_EXPIRY,
        subject_type="quote",
        subject_id=SUBJECT,
        payload={"quote_id": str(SUBJECT)},
    )

    assert timer.status == "scheduled"
    assert timer.due_at == clock.now() + timedelta(days=7)
    assert timer.payload == {"quote_id": str(SUBJECT)}


def test_timer_schedule_without_default_requires_an_explicit_delay(db: Session) -> None:
    service = _timer_service(ManualClock())
    with pytest.raises(ValueError, match="no default delay"):
        service.schedule(db, name="custom.unknown", subject_type="x", subject_id=SUBJECT)
    with pytest.raises(ValueError, match="not both"):
        service.schedule(
            db,
            name=TimerKind.QUOTE_EXPIRY,
            subject_type="x",
            subject_id=SUBJECT,
            delay=timedelta(minutes=1),
            at=datetime.now(UTC),
        )
    with pytest.raises(ValueError, match="negative"):
        service.schedule(
            db,
            name="custom.unknown",
            subject_type="x",
            subject_id=SUBJECT,
            delay=timedelta(seconds=-1),
        )
    with pytest.raises(ValueError, match="timezone-aware"):
        service.schedule(
            db,
            name="custom.unknown",
            subject_type="x",
            subject_id=SUBJECT,
            at=datetime(2026, 1, 1),  # naive on purpose
        )


def test_rescheduling_rearms_the_same_row(db: Session) -> None:
    clock = ManualClock(datetime(2026, 4, 1, 8, 0, tzinfo=UTC))
    service = _timer_service(clock)

    first = service.schedule(
        db,
        name=TimerKind.INQUIRY_SLA,
        subject_type="inquiry",
        subject_id=SUBJECT,
        delay=timedelta(minutes=10),
    )
    second = service.schedule(
        db,
        name=TimerKind.INQUIRY_SLA,
        subject_type="inquiry",
        subject_id=SUBJECT,
        delay=timedelta(minutes=30),
        payload={"version": 2},
    )
    db.flush()

    assert second.id == first.id
    assert second.due_at == clock.now() + timedelta(minutes=30)
    assert second.payload == {"version": 2}
    assert len(list(db.execute(select(WorkflowTimer)).scalars())) == 1


def test_timer_fires_once_at_its_due_time_and_writes_outbox_and_audit(
    db: Session,
) -> None:
    clock = ManualClock(datetime(2026, 4, 1, 8, 0, tzinfo=UTC))
    service = _timer_service(clock)
    timer = service.schedule(
        db,
        name=TimerKind.PAYMENT_REMINDER,
        subject_type="payment",
        subject_id=SUBJECT,
        delay=timedelta(hours=1),
    )

    assert service.fire_due(db) == 0

    clock.advance(timedelta(hours=1))
    assert service.fire_due(db) == 1
    db.flush()

    assert timer.status == "fired"
    assert timer.fired_at == clock.now()
    event = db.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == TIMER_FIRED_EVENT)
    ).scalar_one()
    assert event.payload["name"] == TimerKind.PAYMENT_REMINDER.value
    assert event.payload["subject_id"] == str(SUBJECT)
    assert event.payload["fired_at"] == clock.now().isoformat()

    # Fire-once: a second beat run at the same instant does nothing.
    assert service.fire_due(db) == 0
    assert (
        len(
            list(
                db.execute(
                    select(OutboxEvent).where(OutboxEvent.event_type == TIMER_FIRED_EVENT)
                ).scalars()
            )
        )
        == 1
    )

    actions = audit_actions(db)
    assert "workflow.timer_scheduled" in actions
    assert "workflow.timer_fired" in actions


def test_rescheduling_after_firing_rearms_the_timer(db: Session) -> None:
    clock = ManualClock(datetime(2026, 4, 1, 8, 0, tzinfo=UTC))
    service = _timer_service(clock)
    timer = service.schedule(
        db,
        name=TimerKind.INQUIRY_FOLLOW_UP,
        subject_type="inquiry",
        subject_id=SUBJECT,
        delay=timedelta(hours=1),
    )
    clock.advance(timedelta(hours=1))
    assert service.fire_due(db) == 1

    rearmed = service.schedule(
        db,
        name=TimerKind.INQUIRY_FOLLOW_UP,
        subject_type="inquiry",
        subject_id=SUBJECT,
        delay=timedelta(hours=2),
    )

    assert rearmed.id == timer.id
    assert rearmed.status == "scheduled"
    assert rearmed.fired_at is None
    assert rearmed.due_at == clock.now() + timedelta(hours=2)


def test_cancelled_timer_never_fires(db: Session) -> None:
    clock = ManualClock(datetime(2026, 4, 1, 8, 0, tzinfo=UTC))
    service = _timer_service(clock)
    timer = service.schedule(
        db,
        name=TimerKind.BOOKING_HOLD_EXPIRY,
        subject_type="booking",
        subject_id=SUBJECT,
        delay=timedelta(minutes=30),
    )

    assert service.cancel(
        db,
        name=TimerKind.BOOKING_HOLD_EXPIRY,
        subject_type="booking",
        subject_id=SUBJECT,
    )
    assert not service.cancel(
        db,
        name=TimerKind.BOOKING_HOLD_EXPIRY,
        subject_type="booking",
        subject_id=SUBJECT,
    )

    clock.advance(timedelta(hours=1))
    assert service.fire_due(db) == 0
    assert timer.status == "cancelled"
    assert timer.cancelled_at == datetime(2026, 4, 1, 8, 0, tzinfo=UTC)
    assert "workflow.timer_cancelled" in audit_actions(db)
