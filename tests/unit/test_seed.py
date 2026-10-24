"""Seeding must never run against production data."""

from __future__ import annotations

import pytest

from travelos.admin import seed
from travelos.core.config import Mode, Settings


def test_seed_refuses_live_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    live = Settings(
        _env_file=None,
        mode=Mode.LIVE,
        live_confirmed=True,
        database_url="postgresql+psycopg://user:pass@db:5432/travelos",
        redis_url="redis://redis:6379/0",
        secret_key="c" * 40,
        public_base_url="https://travelos.example.com",
        encryption_key="MDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDAwMDA=",
    )
    monkeypatch.setattr(seed, "get_settings", lambda: live)

    assert seed.main() == 1


def test_seed_is_a_noop_until_generators_exist(monkeypatch: pytest.MonkeyPatch) -> None:
    test = Settings(_env_file=None, mode=Mode.TEST, log_json=True)
    monkeypatch.setattr(seed, "get_settings", lambda: test)

    assert seed.main() == 0
