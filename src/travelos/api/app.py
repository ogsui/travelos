"""FastAPI application factory and process entry point."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import timedelta

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from travelos import __version__
from travelos.api.health import router as health_router
from travelos.api.middleware import RequestContextMiddleware
from travelos.api.routes.admin import router as admin_router
from travelos.api.routes.approvals import autonomy_router
from travelos.api.routes.approvals import router as approvals_router
from travelos.api.routes.auth import router as auth_router
from travelos.api.routes.crm import router as crm_router
from travelos.approval.service import ApprovalService
from travelos.channels.api import router as channels_router
from travelos.core.audit import AuditWriter
from travelos.core.config import Mode, Settings, get_settings
from travelos.core.db import build_engine, build_session_factory
from travelos.core.features import DatabaseFlagSource, FeatureFlagService
from travelos.core.logging import configure_logging, get_request_id
from travelos.security.sessions import AuthSessionService

logger = logging.getLogger("travelos.api")


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the ASGI app. Raises at startup if settings violate live rules."""

    resolved = settings or get_settings()
    configure_logging(resolved.log_level, json_output=resolved.log_json)

    engine = build_engine(resolved.effective_database_url)
    session_factory = build_session_factory(engine)

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
        try:
            yield
        finally:
            engine.dispose()
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
    app.state.engine = engine
    app.state.session_factory = session_factory
    app.state.audit = AuditWriter()
    app.state.auth_sessions = AuthSessionService(
        session_factory, ttl=timedelta(minutes=resolved.session_ttl_minutes)
    )
    app.state.feature_flags = FeatureFlagService(DatabaseFlagSource(session_factory), resolved)
    app.state.approvals = ApprovalService.from_settings(resolved)
    app.add_middleware(RequestContextMiddleware)

    if resolved.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=resolved.cors_origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE"],
            allow_headers=["Authorization", "Content-Type", "X-Request-ID", "X-CSRF-Token"],
        )

    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    app.include_router(approvals_router)
    app.include_router(crm_router)
    app.include_router(channels_router)
    app.include_router(autonomy_router)

    @app.exception_handler(HTTPException)
    async def http_exception(_request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content={"error": exc.detail},
            headers=exc.headers,
        )

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
