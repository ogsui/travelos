"""FastAPI application factory and process entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from travelos import __version__
from travelos.api.health import router as health_router
from travelos.api.middleware import RequestContextMiddleware
from travelos.core.config import Mode, Settings, get_settings
from travelos.core.logging import configure_logging, get_request_id

logger = logging.getLogger("travelos.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI app. Raises at startup if settings violate live rules."""

    resolved = settings or get_settings()
    configure_logging(resolved.log_level, json_output=resolved.log_json)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        logger.info(
            "service starting",
            extra={
                "mode": resolved.mode.value,
                "environment": resolved.environment,
                "version": __version__,
                "kill_switches": list(resolved.kill_switches.enabled()),
            },
        )
        yield
        logger.info("service stopped")

    app = FastAPI(
        title=resolved.app_name,
        version=__version__,
        docs_url=None if resolved.mode is Mode.LIVE else "/docs",
        redoc_url=None,
        openapi_url=None if resolved.mode is Mode.LIVE else "/openapi.json",
        lifespan=lifespan,
    )
    app.state.settings = resolved
    app.add_middleware(RequestContextMiddleware)

    if resolved.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        )

    app.include_router(health_router)

    @app.exception_handler(Exception)
    async def unhandled_exception(request: Request, exc: Exception) -> JSONResponse:
        logger.error("unhandled exception", exc_info=exc)
        request_id = getattr(request.state, "request_id", None) or get_request_id()
        return JSONResponse(
            status_code=500,
            content={"error": "internal_server_error", "request_id": request_id},
        )

    return app


app = create_app()
