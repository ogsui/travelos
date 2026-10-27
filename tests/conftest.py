"""Shared test fixtures. Tests never touch the network or real data."""

from __future__ import annotations

import os
from collections.abc import Iterator

# Must be set before importing the app, whose module-level settings load eagerly.
os.environ["MODE"] = "test"

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from travelos.api.app import create_app
from travelos.core.config import KillSwitches, Mode, Settings, reset_settings_cache
from travelos.security.encryption import StaticKeyProvider, set_key_provider

# A developer's own `.env` must never change what the suite measures: tests set
# every value they care about explicitly (or through monkeypatch).
Settings.model_config["env_file"] = None

TEST_ENCRYPTION_KEY = b"0123456789abcdef0123456789abcdef"


@pytest.fixture(autouse=True)
def _static_encryption_key() -> Iterator[None]:
    """Every test gets a known key; production reads ENCRYPTION_KEY instead."""

    reset_settings_cache()
    set_key_provider(StaticKeyProvider(TEST_ENCRYPTION_KEY))
    yield
    set_key_provider(None)
    reset_settings_cache()


@pytest.fixture
def test_settings() -> Settings:
    return Settings(
        _env_file=None,
        mode=Mode.TEST,
        environment="test",
        log_json=True,
        kill_switches=KillSwitches(),
    )


@pytest.fixture
def app(test_settings: Settings) -> FastAPI:
    return create_app(test_settings)


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client
