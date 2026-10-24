"""Liveness and readiness endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from travelos import __version__
from travelos.core.config import Mode, Settings

router = APIRouter(tags=["ops"])


class HealthResponse(BaseModel):
    status: Literal["ok"] = "ok"
    service: str
    version: str
    mode: Mode


class ReadyResponse(BaseModel):
    status: Literal["ready"] = "ready"
    service: str
    mode: Mode
    checks: dict[str, str] = Field(default_factory=dict)


def _settings(request: Request) -> Settings:
    return request.app.state.settings  # type: ignore[no-any-return]


@router.get("/health", response_model=HealthResponse)
def health(request: Request) -> HealthResponse:
    """Liveness: the process is up and serving requests."""

    settings = _settings(request)
    return HealthResponse(service=settings.app_name, version=__version__, mode=settings.mode)


@router.get("/ready", response_model=ReadyResponse)
def ready(request: Request) -> ReadyResponse:
    """Readiness: configuration is valid for the current mode.

    Database and Redis checks are added by their owning tasks (002/004); this
    endpoint must stay cheap because container orchestrators poll it.
    """

    settings = _settings(request)
    return ReadyResponse(
        service=settings.app_name,
        mode=settings.mode,
        checks={"config": "ok"},
    )
