"""Celery application for background jobs (workers and beat)."""

from __future__ import annotations

from celery import Celery

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
)


@celery_app.task(name="travelos.ping")  # type: ignore[untyped-decorator]
def ping() -> str:
    """Smoke-test task used by the worker health check and runbook."""

    return "pong"
