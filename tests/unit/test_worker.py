"""Worker/Celery wiring tests that do not need a broker."""

from __future__ import annotations

from travelos.core.config import get_settings
from travelos.worker.celery_app import celery_app, ping


def test_celery_uses_configured_redis() -> None:
    assert celery_app.conf.broker_url == get_settings().effective_redis_url
    assert celery_app.conf.task_serializer == "json"
    assert celery_app.conf.accept_content == ["json"]


def test_ping_task_is_registered_and_runs() -> None:
    assert "travelos.ping" in celery_app.tasks
    assert ping.run() == "pong"
