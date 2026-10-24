"""HTTP surface tests: health, readiness, request IDs and error handling."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from travelos import __version__
from travelos.api.app import create_app
from travelos.core.config import Mode, Settings

pytestmark = pytest.mark.integration


def test_health(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "service": "TravelOS",
        "version": __version__,
        "mode": "test",
    }


def test_ready(client: TestClient) -> None:
    response = client.get("/ready")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ready",
        "service": "TravelOS",
        "mode": "test",
        "checks": {"config": "ok"},
    }


def test_request_id_is_generated(client: TestClient) -> None:
    response = client.get("/health")

    request_id = response.headers["X-Request-ID"]
    assert len(request_id) == 32
    assert request_id.isalnum()


def test_request_id_is_echoed_when_valid(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "test-request-123"})

    assert response.headers["X-Request-ID"] == "test-request-123"


def test_request_id_is_replaced_when_invalid(client: TestClient) -> None:
    response = client.get("/health", headers={"X-Request-ID": "<script>alert(1)</script>"})

    assert response.headers["X-Request-ID"] != "<script>alert(1)</script>"
    assert len(response.headers["X-Request-ID"]) == 32


def test_security_headers(client: TestClient) -> None:
    response = client.get("/health")

    assert response.headers["X-Content-Type-Options"] == "nosniff"
    assert response.headers["X-Frame-Options"] == "DENY"
    assert response.headers["Referrer-Policy"] == "strict-origin-when-cross-origin"


def test_docs_available_outside_live(client: TestClient) -> None:
    assert client.get("/docs").status_code == 200
    assert client.get("/openapi.json").status_code == 200


def test_cors_allowlist() -> None:
    settings = Settings(
        _env_file=None,
        mode=Mode.TEST,
        cors_origins=["http://localhost:3000"],
    )
    app = create_app(settings)

    with TestClient(app) as client:
        response = client.get("/health", headers={"Origin": "http://localhost:3000"})

    assert response.headers["access-control-allow-origin"] == "http://localhost:3000"


def test_unhandled_exception_returns_request_id(test_settings: Settings) -> None:
    app = create_app(test_settings)

    @app.get("/boom")
    def boom() -> None:
        raise RuntimeError("synthetic failure for test")

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/boom", headers={"X-Request-ID": "boom-request-123"})

    assert response.status_code == 500
    assert response.json() == {
        "error": "internal_server_error",
        "request_id": "boom-request-123",
    }


def test_live_hides_docs() -> None:
    settings = Settings(
        _env_file=None,
        mode=Mode.LIVE,
        live_confirmed=True,
        database_url="postgresql+psycopg://user:pass@db:5432/travelos",
        redis_url="redis://redis:6379/0",
        secret_key="b" * 40,
        public_base_url="https://travelos.example.com",
    )

    app: FastAPI = create_app(settings)

    assert app.docs_url is None
    assert app.openapi_url is None
    with TestClient(app) as client:
        assert client.get("/health").json()["mode"] == "live"
        assert client.get("/docs").status_code == 404
