"""Seeding refuses live mode and reports policy work without needing a database."""

from __future__ import annotations

import pytest
from sqlalchemy.exc import SQLAlchemyError

from travelos.admin import seed
from travelos.approval.service import SeedReport
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


def test_seed_reports_the_policy_report(monkeypatch: pytest.MonkeyPatch) -> None:
    test = Settings(_env_file=None, mode=Mode.TEST, log_json=True)
    monkeypatch.setattr(seed, "get_settings", lambda: test)
    monkeypatch.setattr(
        seed,
        "seed_autonomy_policies",
        lambda _settings: SeedReport(inserted=45, repaired=2),
    )

    assert seed.main() == 0


def test_seed_survives_an_unavailable_database(monkeypatch: pytest.MonkeyPatch) -> None:
    test = Settings(_env_file=None, mode=Mode.TEST, log_json=True)
    monkeypatch.setattr(seed, "get_settings", lambda: test)

    def unavailable(_settings: Settings) -> SeedReport:
        raise SQLAlchemyError("synthetic connection failure")

    monkeypatch.setattr(seed, "seed_autonomy_policies", unavailable)

    assert seed.main() == 0
