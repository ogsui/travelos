"""Shared FastAPI dependencies: database sessions, authentication, permissions.

``require(Permission.X)`` is the permission gate used by every protected route.
It authenticates the caller, enforces cookie CSRF rules, and audits denials
before returning 403.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable, Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status
from sqlalchemy.orm import Session

from travelos.core.audit import Actor, AuditWriter
from travelos.core.config import Settings
from travelos.core.features import FeatureFlagService
from travelos.core.models import AuthSession, User
from travelos.security.permissions import Permission, has_permission
from travelos.security.sessions import (
    CSRF_COOKIE,
    CSRF_HEADER,
    SAFE_METHODS,
    SESSION_COOKIE,
    AuthSessionService,
)


@dataclass(frozen=True, slots=True)
class AuthContext:
    user: User
    session: AuthSession
    via_cookie: bool


def get_app_settings(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def get_db(request: Request) -> Iterator[Session]:
    """A request-scoped session. Handlers commit explicitly."""

    db: Session = request.app.state.session_factory()
    try:
        yield db
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


def get_auth_service(request: Request) -> AuthSessionService:
    service: AuthSessionService = request.app.state.auth_sessions
    return service


def get_audit_writer(request: Request) -> AuditWriter:
    writer: AuditWriter = request.app.state.audit
    return writer


def get_feature_flags(request: Request) -> FeatureFlagService:
    service: FeatureFlagService = request.app.state.feature_flags
    return service


def client_ip(request: Request) -> str | None:
    return request.client.host[:45] if request.client else None


def _unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail=detail,
        headers={"WWW-Authenticate": "Bearer"},
    )


def _extract_token(request: Request) -> tuple[str, bool] | None:
    header = request.headers.get("Authorization", "")
    scheme, _, credentials = header.partition(" ")
    if scheme.lower() == "bearer" and credentials.strip():
        return credentials.strip(), False
    cookie = request.cookies.get(SESSION_COOKIE)
    if cookie:
        return cookie, True
    return None


def _check_csrf(request: Request) -> None:
    if request.method in SAFE_METHODS:
        return
    cookie = request.cookies.get(CSRF_COOKIE, "")
    header = request.headers.get(CSRF_HEADER, "")
    if not cookie or not header or not secrets.compare_digest(cookie, header):
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="csrf_failed")


def get_current_auth(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[AuthSessionService, Depends(get_auth_service)],
) -> AuthContext:
    extracted = _extract_token(request)
    if extracted is None:
        raise _unauthorized("authentication_required")
    token, via_cookie = extracted
    auth_session = service.resolve(db, token)
    if auth_session is None or not auth_session.user.is_active:
        raise _unauthorized("invalid_session")
    if via_cookie:
        _check_csrf(request)
    return AuthContext(user=auth_session.user, session=auth_session, via_cookie=via_cookie)


def require(*permissions: Permission) -> Callable[..., AuthContext]:
    """FastAPI dependency factory: caller must hold every listed permission."""

    def dependency(
        request: Request,
        auth: Annotated[AuthContext, Depends(get_current_auth)],
        db: Annotated[Session, Depends(get_db)],
        audit: Annotated[AuditWriter, Depends(get_audit_writer)],
    ) -> AuthContext:
        missing = [p for p in permissions if not has_permission(auth.user.role, p)]
        if missing:
            audit.record(
                db,
                action="auth.permission_denied",
                actor=Actor.user(auth.user.id),
                subject_type="route",
                after={
                    "method": request.method,
                    "path": request.url.path,
                    "permissions": [p.value for p in missing],
                },
                ip=client_ip(request),
            )
            db.commit()
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="permission_denied",
            )
        return auth

    return dependency
