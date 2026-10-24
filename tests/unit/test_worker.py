"""Worker/Celery wiring tests that do not need a broker."""

from __future__ import annotations

import pytest

from travelos.core.config import get_settings
from travelos.worker import runtime
from travelos.worker.celery_app import celery_app, ping
from travelos.worker.tasks import HANDLERS, register_handler


def test_celery_uses_configured_redis() -> None:
    assert celery_app.conf.broker_url == get_settings().effective_redis_url
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]


def test_ping_task_is_registered_and_runs() -> None:
    assert "travelos.ping" in celery_app.tasks
    assert ping.run() == "pong"


def test_workflow_tasks_are_registered() -> None:
    for name in (
        "travelos.outbox.dispatch",
        "travelos.timers.fire_due",
        "travelos.sessions.purge_expired",
        "travelos.llm.purge_expired",
        "travelos.autonomy.demote",
    ):
        assert name in celery_app.tasks, name


def test_beat_schedule_runs_outbox_and_timers_regularly() -> None:
    schedule = celery_app.conf.beat_schedule
    settings = get_settings()

    assert schedule["outbox-dispatch"]["task"] == "travelos.outbox.dispatch"
    assert schedule["outbox-dispatch"]["schedule"] == settings.outbox_poll_seconds
    assert schedule["fire-due-timers"]["task"] == "travelos.timers.fire_due"
    assert schedule["fire-due-timers"]["schedule"] == settings.timer_poll_seconds
    assert schedule["purge-expired-sessions"]["task"] == "travelos.sessions.purge_expired"
    assert schedule["purge-expired-llm-calls"]["task"] == "travelos.llm.purge_expired"
    assert schedule["autonomy-demote"]["task"] == "travelos.autonomy.demote"


def test_handler_registration_is_explicit_and_conflict_free() -> None:
    def handler(_db, _event) -> None:  # type: ignore[no-untyped-def]
        return None

    register_handler("test.event", handler)
    assert HANDLERS["test.event"] is handler

    with pytest.raises(ValueError, match="already registered"):

        def other(_db, _event) -> None:  # type: ignore[no-untyped-def]
            return None

        register_handler("test.event", other)


def test_runtime_caches_one_engine_per_process() -> None:
    runtime.reset_worker_runtime()
    try:
        engine = runtime.get_engine()
        assert runtime.get_engine() is engine
        assert runtime.get_session_factory() is runtime.get_session_factory()
    finally:
        runtime.reset_worker_runtime()
    assert runtime.get_engine.cache_info().currsize == 0
