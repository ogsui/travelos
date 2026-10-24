"""Server-side sessions.

Opaque 256-bit tokens are sent to the client; only their SHA-256 hash is stored,
so a database leak cannot be replayed as a login. Sessions are revoked by
setting ``revoked_at`` and expire after the configured TTL.
"""

from __future__ import annotations

import hashlib
import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from travelos.core.models import AuthSession, User

SESSION_COOKIE = "travelos_session"
CSRF_COOKIE = "travelos_csrf"
CSRF_HEADER = "x-csrf-token"
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})
_TOKEN_BYTES = 32
_MAX_USER_AGENT = 500


def new_token() -> str:
    """A URL-safe session token with 256 bits of entropy."""

    return secrets.token_urlsafe(_TOKEN_BYTES)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_csrf_token() -> str:
    return secrets.token_urlsafe(32)


class AuthSessionService:
    def __init__(
        self,
        session_factory: sessionmaker[Session],
        *,
        ttl: timedelta,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("session ttl must be positive")
        self._session_factory = session_factory
        self._ttl = ttl
        self._now = now or (lambda: datetime.now(UTC))

    def create(
        self,
        db: Session,
        *,
        user: User,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> tuple[AuthSession, str]:
        token = new_token()
        row = AuthSession(
            user_id=user.id,
            token_hash=hash_token(token),
            expires_at=self._now() + self._ttl,
            ip=ip,
            user_agent=(user_agent or "")[:_MAX_USER_AGENT] or None,
        )
        db.add(row)
        db.flush()
        return row, token

    def resolve(self, db: Session, token: str | None) -> AuthSession | None:
        if not token:
            return None
        return (
            db.execute(
                select(AuthSession).where(
                    AuthSession.token_hash == hash_token(token),
                    AuthSession.revoked_at.is_(None),
                    AuthSession.expires_at > self._now(),
                )
            )
            .scalars()
            .first()
        )

    def revoke(self, db: Session, auth_session: AuthSession) -> None:
        if auth_session.revoked_at is None:
            auth_session.revoked_at = self._now()
            db.flush()

    def purge_expired(self, db: Session) -> int:
        """Delete expired or revoked sessions older than a day (housekeeping)."""

        cutoff = self._now() - timedelta(days=1)
        rows = db.execute(
            select(AuthSession).where(
                (AuthSession.expires_at < cutoff) | (AuthSession.revoked_at < cutoff)
            )
        ).scalars()
        deleted = 0
        for row in rows:
            db.delete(row)
            deleted += 1
        return deleted
