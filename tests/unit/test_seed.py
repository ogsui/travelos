"""Seeding refuses live mode and reports policy/CRM work without a database."""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy.exc import SQLAlchemyError

from travelos.admin import seed
from travelos.approval.service import SeedReport
from travelos.core.config import Mode, Settings
from travelos.synthetic.generator import SyntheticDataset
from travelos.synthetic.loader import SeedCrmReport


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
        llm_provider_chain=["openai"],
        llm_openai_api_key="sk-synthetic-not-a-real-key",
        llm_openai_model="owner-selected-model",
        llm_daily_budget_paise=1,
    )
    monkeypatch.setattr(seed, "get_settings", lambda: live)

    def must_not_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("live mode must not write synthetic fixtures")

    monkeypatch.setattr(seed, "write_dataset", must_not_write)

    assert seed.main() == 1


def test_seed_refuses_shadow_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    shadow = Settings(_env_file=None, mode=Mode.SHADOW)
    monkeypatch.setattr(seed, "get_settings", lambda: shadow)

    def must_not_write(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("shadow mode must not write synthetic fixtures")

    monkeypatch.setattr(seed, "write_dataset", must_not_write)

    assert seed.main() == 1


def test_seed_reports_the_policy_and_crm_report(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    test = Settings(_env_file=None, mode=Mode.TEST, log_json=True)
    dataset = SyntheticDataset(seed=test.synthetic_seed, generator_version=1)
    written: list[tuple[SyntheticDataset, Path, Path | None]] = []

    monkeypatch.setattr(seed, "get_settings", lambda: test)
    monkeypatch.setattr(seed, "generate_dataset", lambda _seed: dataset)
    monkeypatch.setattr(
        seed,
        "write_dataset",
        lambda value, directory, *, eval_directory=None: (
            written.append((value, Path(directory), eval_directory)) or {"files": []}
        ),
    )
    monkeypatch.setattr(
        seed,
        "seed_autonomy_policies",
        lambda _settings: SeedReport(inserted=45, repaired=2),
    )
    monkeypatch.setattr(
        seed,
        "seed_synthetic_crm",
        lambda _settings, _dataset: SeedCrmReport(customers=500, contacts=10),
    )

    assert seed.main() == 0
    root = seed.data_root(test)
    assert written == [
        (dataset, root / "synthetic", root / "eval"),
    ]


def test_data_root_uses_the_data_dir_override(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, mode=Mode.TEST, data_dir=str(tmp_path))
    assert seed.data_root(settings) == tmp_path
    assert seed.data_root(Settings(_env_file=None, mode=Mode.TEST)) == (seed.REPO_ROOT / "data")


def test_seed_survives_an_unavailable_database(monkeypatch: pytest.MonkeyPatch) -> None:
    test = Settings(_env_file=None, mode=Mode.TEST, log_json=True)
    dataset = SyntheticDataset(seed=test.synthetic_seed, generator_version=1)

    monkeypatch.setattr(seed, "get_settings", lambda: test)
    monkeypatch.setattr(seed, "generate_dataset", lambda _seed: dataset)
    monkeypatch.setattr(seed, "write_dataset", lambda *_args, **_kwargs: {"files": []})

    def unavailable(_settings: Settings) -> SeedReport:
        raise SQLAlchemyError("synthetic connection failure")

    monkeypatch.setattr(seed, "seed_autonomy_policies", unavailable)

    def must_not_seed_crm(_settings: Settings, _dataset: SyntheticDataset) -> SeedCrmReport:
        raise AssertionError("CRM seeding must not run after the policy seed failed")

    monkeypatch.setattr(seed, "seed_synthetic_crm", must_not_seed_crm)

    assert seed.main() == 0
