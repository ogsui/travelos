"""Login, session, logout and TOTP enrollment endpoints.

Every outcome that matters to security (success, bad password, MFA failure,
enrollment requirement, logout) writes an audit row. Passwords and TOTP secrets
never enter audit rows or logs.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from travelos.api.deps import (
    AuthContext,
    client_ip,
    get_app_settings,
    get_audit_writer,
    get_auth_service,
    get_current_auth,
    get_db,
)
from travelos.api.schemas import (
    LoginRequest,
    LoginResponse,
    MfaEnrollRequest,
    MfaEnrollResponse,
    UserOut,
)
from travelos.core.audit import Actor, AuditWriter
from travelos.core.config import Settings
from travelos.core.models import User
from travelos.security.passwords import (
    hash_password,
    needs_rehash,
    verify_password_or_dummy,
)
from travelos.security.permissions import requires_mfa
from travelos.security.sessions import (
    CSRF_COOKIE,
    SESSION_COOKIE,
    AuthSessionService,
    new_csrf_token,
)
from travelos.security.totp import generate_secret, provisioning_uri, verify_code

router = APIRouter(prefix="/auth", tags=["auth"])


def user_out(user: User) -> UserOut:
    return UserOut(
        id=user.id,
        email=user.email,
        role=user.role,
        is_active=user.is_active,
        mfa_enrolled=user.mfa_secret is not None,
    )


def _find_user(db: Session, email: str) -> User | None:
    return (
        db.execute(
            select(User).where(
                func.lower(User.email) == email,
                User.is_active.is_(True),
            )
        )
        .scalars()
        .first()
    )


def _audit_failure(
    db: Session,
    audit: AuditWriter,
    request: Request,
    *,
    action: str,
    reason: str,
    user: User | None,
) -> None:
    audit.record(
        db,
        action=action,
        actor=Actor.user(user.id) if user is not None else Actor.system(),
        subject_type="user" if user is not None else None,
        subject_id=user.id if user is not None else None,
        after={"reason": reason},
        ip=client_ip(request),
    )
    db.commit()


def _set_auth_cookies(response: Response, settings: Settings, token: str, csrf: str) -> None:
    max_age = settings.session_ttl_minutes * 60
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=max_age,
        httponly=True,
        samesite="lax",
        secure=settings.secure_cookies,
        path="/",
    )
    response.set_cookie(
        CSRF_COOKIE,
        csrf,
        max_age=max_age,
        httponly=False,
        samesite="lax",
        secure=settings.secure_cookies,
        path="/",
    )


@router.post("/login", response_model=LoginResponse)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    sessions: Annotated[AuthSessionService, Depends(get_auth_service)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
) -> LoginResponse:
    user = _find_user(db, payload.email)
    password = payload.password.get_secret_value()
    if not verify_password_or_dummy(password, user.password_hash if user else None):
        _audit_failure(
            db,
            audit,
            request,
            action="auth.login_failed",
            reason="invalid_credentials" if user is None else "bad_password",
            user=user,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")

    assert user is not None  # verify_password_or_dummy only passes for a real user
    secret = user.mfa_secret
    if secret is not None:
        if not payload.totp_code:
            _audit_failure(
                db, audit, request, action="auth.mfa_failed", reason="code_missing", user=user
            )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="mfa_required")
        if not verify_code(secret, payload.totp_code):
            _audit_failure(
                db, audit, request, action="auth.mfa_failed", reason="code_invalid", user=user
            )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_mfa_code")
    elif requires_mfa(user.role):
        _audit_failure(
            db,
            audit,
            request,
            action="auth.mfa_enrollment_required",
            reason="role_requires_mfa",
            user=user,
        )
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="mfa_enrollment_required")

    auth_session, token = sessions.create(
        db,
        user=user,
        ip=client_ip(request),
        user_agent=request.headers.get("User-Agent"),
    )
    csrf = new_csrf_token()
    user.last_login_at = datetime.now(UTC)
    if needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
    audit.record(
        db,
        action="auth.login",
        actor=Actor.user(user.id),
        subject_type="user",
        subject_id=user.id,
        ip=client_ip(request),
    )
    db.commit()

    _set_auth_cookies(response, settings, token, csrf)
    return LoginResponse(
        user=user_out(user),
        access_token=token,
        expires_at=auth_session.expires_at,
        csrf_token=csrf,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    request: Request,
    response: Response,
    auth: Annotated[AuthContext, Depends(get_current_auth)],
    db: Annotated[Session, Depends(get_db)],
    sessions: Annotated[AuthSessionService, Depends(get_auth_service)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
) -> None:
    sessions.revoke(db, auth.session)
    audit.record(
        db,
        action="auth.logout",
        actor=Actor.user(auth.user.id),
        subject_type="user",
        subject_id=auth.user.id,
        ip=client_ip(request),
    )
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")
    response.delete_cookie(CSRF_COOKIE, path="/")


@router.get("/me", response_model=UserOut)
def me(auth: Annotated[AuthContext, Depends(get_current_auth)]) -> UserOut:
    return user_out(auth.user)


@router.post("/mfa/enroll", response_model=MfaEnrollResponse)
def enroll_mfa(
    payload: MfaEnrollRequest,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
) -> MfaEnrollResponse:
    """Issue a new TOTP secret after a password check.

    Rotating an existing secret requires a valid current code, so a stolen
    password alone cannot replace the second factor.
    """

    user = _find_user(db, payload.email)
    password = payload.password.get_secret_value()
    if not verify_password_or_dummy(password, user.password_hash if user else None):
        _audit_failure(
            db,
            audit,
            request,
            action="auth.mfa_enroll_failed",
            reason="invalid_credentials",
            user=user,
        )
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_credentials")

    assert user is not None
    action = "auth.mfa_enrolled"
    if user.mfa_secret is not None:
        action = "auth.mfa_rotated"
        if not payload.totp_code or not verify_code(user.mfa_secret, payload.totp_code):
            _audit_failure(
                db,
                audit,
                request,
                action="auth.mfa_enroll_failed",
                reason="rotating_secret_requires_valid_code",
                user=user,
            )
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid_mfa_code")

    secret = generate_secret()
    user.mfa_secret = secret
    audit.record(
        db,
        action=action,
        actor=Actor.user(user.id),
        subject_type="user",
        subject_id=user.id,
        ip=client_ip(request),
    )
    db.commit()

    return MfaEnrollResponse(
        secret=secret,
        otpauth_uri=provisioning_uri(secret, user.email, issuer=settings.mfa_issuer),
    )
