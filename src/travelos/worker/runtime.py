"""Lazy process-wide database wiring for Celery workers.

Workers are long-lived processes: one engine and session factory per process.
Nothing connects until a task actually runs, so importing the Celery app in a
test or during Django-style discovery does not open sockets.
"""

from __future__ import annotations

from functools import lru_cache

from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from travelos.core.config import get_settings
from travelos.core.db import build_engine, build_session_factory


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    settings = get_settings()
    return build_engine(settings.effective_database_url)


@lru_cache(maxsize=1)
def get_session_factory() -> sessionmaker[Session]:
    return build_session_factory(get_engine())


def reset_worker_runtime() -> None:
    """Dispose the cached engine (tests and config reloads)."""

    if get_engine.cache_info().currsize:
        get_engine().dispose()
    get_session_factory.cache_clear()
    get_engine.cache_clear()
