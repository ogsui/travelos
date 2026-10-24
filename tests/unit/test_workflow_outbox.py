"""Clock, backoff and timer-default unit tests (no database)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from travelos.core.clock import ManualClock, SystemClock
from travelos.workflow.outbox import BackoffPolicy, DispatchReport, IdempotentConsumer, OutboxWriter
from travelos.workflow.timers import DEFAULT_TIMER_DELAYS, TimerKind, default_delay


def test_system_clock_is_utc_aware() -> None:
    now = SystemClock().now()
    assert now.tzinfo is not None
    assert now.utcoffset() == timedelta(0)


def test_manual_clock_only_moves_when_told() -> None:
    start = datetime(2026, 1, 1, 9, 0, tzinfo=UTC)
    clock = ManualClock(start)
    assert clock.now() == start
    clock.advance(timedelta(minutes=90))
    assert clock.now() == start + timedelta(minutes=90)
    clock.set(start)
    assert clock.now() == start


def test_manual_clock_rejects_naive_datetimes() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ManualClock(datetime(2026, 1, 1))  # naive on purpose
    clock = ManualClock()
    with pytest.raises(ValueError, match="timezone-aware"):
        clock.set(datetime(2026, 1, 1))  # naive on purpose


def test_backoff_is_exponential_bounded_and_jitter_free_when_asked() -> None:
    policy = BackoffPolicy(10, 80, jitter=0.0, random_source=lambda: 0.5)

    assert policy.delay(1) == timedelta(seconds=10)
    assert policy.delay(2) == timedelta(seconds=20)
    assert policy.delay(3) == timedelta(seconds=40)
    assert policy.delay(4) == timedelta(seconds=80)
    assert policy.delay(10) == timedelta(seconds=80)


def test_backoff_jitter_stays_within_bounds() -> None:
    low = BackoffPolicy(100, 100, jitter=0.2, random_source=lambda: 0.0)
    high = BackoffPolicy(100, 100, jitter=0.2, random_source=lambda: 1.0)
    middle = BackoffPolicy(100, 100, jitter=0.2, random_source=lambda: 0.5)

    assert low.delay(1) == timedelta(seconds=80)
    assert middle.delay(1) == timedelta(seconds=100)
    assert high.delay(1) == timedelta(seconds=120)


def test_backoff_validation() -> None:
    with pytest.raises(ValueError, match="base_seconds"):
        BackoffPolicy(0, 10)
    with pytest.raises(ValueError, match="max_seconds"):
        BackoffPolicy(10, 5)
    with pytest.raises(ValueError, match="jitter"):
        BackoffPolicy(10, 100, jitter=1.5)
    with pytest.raises(ValueError, match="attempt"):
        BackoffPolicy(10, 100).delay(0)


def test_dispatch_report_serialises_for_celery() -> None:
    assert DispatchReport(processed=2, succeeded=1, failed=1).as_dict() == {
        "processed": 2,
        "succeeded": 1,
        "failed": 1,
        "dead": 0,
        "skipped": 0,
    }


def test_outbox_writer_rejects_bad_event_types_and_naive_times() -> None:
    writer = OutboxWriter()
    with pytest.raises(ValueError, match="event_type"):
        writer.enqueue(None, event_type="")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="event_type"):
        writer.enqueue(None, event_type="x" * 121)  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="timezone-aware"):
        writer.enqueue(  # type: ignore[arg-type]
            None,
            event_type="inquiry.parsed",
            available_at=datetime(2026, 1, 1),  # naive on purpose
        )


def test_idempotent_consumer_requires_a_scope() -> None:
    with pytest.raises(ValueError, match="scope"):
        IdempotentConsumer("")


def test_timer_defaults_cover_every_kind() -> None:
    assert set(DEFAULT_TIMER_DELAYS) == set(TimerKind)
    for kind in TimerKind:
        assert default_delay(kind.value) == DEFAULT_TIMER_DELAYS[kind]
    assert default_delay("custom.unknown") is None
