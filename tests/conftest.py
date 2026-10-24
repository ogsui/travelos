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
from travelos.core.config import KillSwitches, Mode, Settings


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
