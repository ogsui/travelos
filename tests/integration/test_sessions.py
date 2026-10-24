"""Server-side session tests: hashing, expiry, revocation and housekeeping."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from travelos.core.models import AuthSession, User
from travelos.security.sessions import (
    AuthSessionService,
    hash_token,
    new_csrf_token,
)

pytestmark = pytest.mark.integration


def make_user(db: Session) -> User:
    user = User(
        email="owner@example.test",
        password_hash="synthetic-not-a-real-hash",
        role="owner",
    )
    db.add(user)
    db.flush()
    return user


def make_service(db: Session, *, ttl: timedelta = timedelta(minutes=30)):
    clock = [datetime.now(UTC)]
    service = AuthSessionService(sessionmaker(bind=db.get_bind()), ttl=ttl, now=lambda: clock[0])
    return service, clock


def test_tokens_are_random_and_hashed_before_storage(db: Session) -> None:
    user = make_user(db)
    service, _ = make_service(db)

    row, token = service.create(db, user=user, ip="127.0.0.1", user_agent="pytest")

    assert token
    assert row.token_hash == hash_token(token)
    assert row.token_hash != token
    assert len(row.token_hash) == 64
    assert row.ip == "127.0.0.1"
    assert row.user_agent == "pytest"
    assert row.revoked_at is None


def test_resolve_accepts_a_live_token_and_rejects_others(db: Session) -> None:
    user = make_user(db)
    service, _ = make_service(db)
    _, token = service.create(db, user=user)

    resolved = service.resolve(db, token)

    assert resolved is not None
    assert resolved.user_id == user.id
    assert resolved.user.email == "owner@example.test"
    assert service.resolve(db, "not-a-real-token") is None
    assert service.resolve(db, "") is None
    assert service.resolve(db, None) is None


def test_expired_sessions_do_not_resolve(db: Session) -> None:
    user = make_user(db)
    service, clock = make_service(db, ttl=timedelta(minutes=30))
    _, token = service.create(db, user=user)

    clock[0] += timedelta(minutes=31)

    assert service.resolve(db, token) is None


def test_revoked_sessions_do_not_resolve_and_revoke_is_idempotent(db: Session) -> None:
    user = make_user(db)
    service, _ = make_service(db)
    row, token = service.create(db, user=user)
    assert service.resolve(db, token) is not None

    service.revoke(db, row)
    revoked_at = row.revoked_at
    service.revoke(db, row)

    assert row.revoked_at == revoked_at
    assert service.resolve(db, token) is None


def test_purge_removes_old_expired_and_revoked_sessions(db: Session) -> None:
    user = make_user(db)
    service, clock = make_service(db)
    _, live_token = service.create(db, user=user)
    _, old_token = service.create(db, user=user)
    revoked_row, _ = service.create(db, user=user)
    service.revoke(db, revoked_row)

    clock[0] += timedelta(days=2)

    assert service.resolve(db, live_token) is None
    assert service.resolve(db, old_token) is None
    deleted = service.purge_expired(db)
    db.flush()

    assert deleted == 3
    assert db.execute(select(func.count()).select_from(AuthSession)).scalar_one() == 0


def test_purge_keeps_recently_expired_sessions_for_forensics(db: Session) -> None:
    user = make_user(db)
    service, clock = make_service(db, ttl=timedelta(minutes=30))
    service.create(db, user=user)

    clock[0] += timedelta(hours=2)
    deleted = service.purge_expired(db)

    assert deleted == 0
    assert db.execute(select(func.count()).select_from(AuthSession)).scalar_one() == 1


def test_non_positive_ttl_is_rejected(db: Session) -> None:
    with pytest.raises(ValueError, match="positive"):
        AuthSessionService(sessionmaker(bind=db.get_bind()), ttl=timedelta(0))


def test_csrf_tokens_are_random() -> None:
    assert new_csrf_token() != new_csrf_token()
