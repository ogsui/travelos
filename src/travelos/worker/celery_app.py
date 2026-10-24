"""Celery application for background jobs (workers and beat)."""

from __future__ import annotations

from celery import Celery
from celery.schedules import crontab

from travelos.core.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "travelos",
    broker=_settings.effective_redis_url,
    backend=_settings.effective_redis_url,
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone=_settings.timezone,
    enable_utc=True,
    task_track_started=True,
    broker_connection_retry_on_startup=True,
    task_default_queue="travelos",
    beat_schedule={
        "outbox-dispatch": {
            "task": "travelos.outbox.dispatch",
            "schedule": _settings.outbox_poll_seconds,
        },
        "fire-due-timers": {
            "task": "travelos.timers.fire_due",
            "schedule": _settings.timer_poll_seconds,
        },
        "purge-expired-sessions": {
            "task": "travelos.sessions.purge_expired",
            "schedule": crontab(hour=3, minute=15),
        },
        "autonomy-demote": {
            "task": "travelos.autonomy.demote",
            "schedule": crontab(hour=2, minute=30),
        },
    },
)


@celery_app.task(name="travelos.ping")  # type: ignore[untyped-decorator]
def ping() -> str:
    """Smoke-test task used by the worker health check and runbook."""

    return "pong"


# Importing the module registers the beat tasks on this app. It imports
# ``celery_app`` back, which is safe because the object exists by now.
import travelos.worker.tasks  # noqa: E402, F401  isort: skip
