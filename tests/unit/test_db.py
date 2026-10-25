"""Database engine construction keeps optional timeouts scoped to short jobs."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlalchemy.engine import Engine

from travelos.core import db


def test_build_engine_uses_short_job_connect_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    expected = MagicMock(spec=Engine)

    def fake_create_engine(*args: object, **kwargs: object) -> Engine:
        calls.append((args, kwargs))
        return expected

    monkeypatch.setattr(db, "create_engine", fake_create_engine)

    result = db.build_engine("postgresql+psycopg://synthetic", connect_timeout=5)

    assert result is expected
    assert calls[0][1]["connect_args"] == {"connect_timeout": 5}


def test_build_engine_leaves_service_timeout_unset(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[tuple[object, ...], dict[str, object]]] = []
    expected = MagicMock(spec=Engine)

    def fake_create_engine(*args: object, **kwargs: object) -> Engine:
        calls.append((args, kwargs))
        return expected

    monkeypatch.setattr(db, "create_engine", fake_create_engine)

    db.build_engine("postgresql+psycopg://synthetic")

    assert "connect_args" not in calls[0][1]
