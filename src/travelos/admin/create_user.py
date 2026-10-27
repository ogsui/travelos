"""Create one staff login (bootstrap for local/test).

There is no user-management screen yet, and a freshly seeded database has no
``users`` row, so nobody can log in. This command creates exactly one account,
hashes the password with argon2id, audits the creation, and prints a generated
password once.

It refuses every mode except ``test``: real staff accounts need the
owner-approved onboarding flow (password policy, recovery, MFA enrolment) that
is still open in ``docs/BACKLOG.md``. Do not loosen this check to get a login.
"""

from __future__ import annotations

import argparse
import logging
import secrets
import string
from collections.abc import Sequence
from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from travelos.core.audit import Actor, AuditWriter
from travelos.core.config import Mode, Settings, get_settings
from travelos.core.db import build_engine, build_session_factory, session_scope
from travelos.core.logging import configure_logging
from travelos.core.models import User
from travelos.security.passwords import MIN_PASSWORD_LENGTH, hash_password

logger = logging.getLogger("travelos.admin.create_user")

ROLES: tuple[str, ...] = ("owner", "admin", "agent", "accountant")
_ALPHABET = string.ascii_letters + string.digits + "!@#$%^&*-_=+"


class CreateUserError(Exception):
    """The account cannot be created; the message is safe to show."""


@dataclass(frozen=True, slots=True)
class CreatedUser:
    """What the caller needs after the session is closed."""

    id: UUID
    email: str
    role: str


def normalise_email(email: str) -> str:
    resolved = email.strip().lower()
    if not resolved or "@" not in resolved or len(resolved) > 320:
        raise CreateUserError(f"invalid email address: {email!r}")
    return resolved


def validate_role(role: str) -> str:
    resolved = role.strip().lower()
    if resolved not in ROLES:
        raise CreateUserError(f"unknown role {role!r}; choose one of {', '.join(ROLES)}")
    return resolved


def validate_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise CreateUserError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    return password


def generate_password(length: int = 20) -> str:
    if length < MIN_PASSWORD_LENGTH:
        raise CreateUserError(f"generated passwords need at least {MIN_PASSWORD_LENGTH} characters")
    return "".join(secrets.choice(_ALPHABET) for _ in range(length))


def create_user(
    settings: Settings,
    *,
    email: str,
    role: str = "owner",
    password: str | None = None,
    audit: AuditWriter | None = None,
) -> tuple[CreatedUser, str]:
    """Insert one user and return it with the plaintext password (once)."""

    if settings.mode is not Mode.TEST:
        raise CreateUserError(
            "refused: accounts may only be bootstrapped in MODE=test; "
            "real staff onboarding is an owner-approved flow"
        )
    resolved_email = normalise_email(email)
    resolved_role = validate_role(role)
    plaintext = validate_password(password) if password else generate_password()

    engine = build_engine(settings.effective_database_url, connect_timeout=5)
    try:
        factory = build_session_factory(engine)
        with session_scope(factory) as db:
            existing = db.execute(
                select(User).where(User.email == resolved_email)
            ).scalar_one_or_none()
            if existing is not None:
                raise CreateUserError(f"a user with email {resolved_email!r} already exists")
            user = User(
                email=resolved_email,
                password_hash=hash_password(plaintext),
                role=resolved_role,
                is_active=True,
            )
            db.add(user)
            db.flush()
            (audit or AuditWriter()).record(
                db,
                action="user.created",
                actor=Actor.system(),
                subject_type="user",
                subject_id=user.id,
                after={"email": resolved_email, "role": resolved_role, "source": "cli"},
            )
            user_id = user.id
    except IntegrityError as exc:  # unique (tenant_id, email)
        raise CreateUserError(f"a user with email {resolved_email!r} already exists") from exc
    finally:
        engine.dispose()

    # Read the row back in a fresh session so callers get a detached object.
    return CreatedUser(id=user_id, email=resolved_email, role=resolved_role), plaintext


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m travelos.admin.create_user",
        description="Create one staff login in MODE=test and print its password once.",
    )
    parser.add_argument("--email", required=True, help="login email (stored lower-case)")
    parser.add_argument("--role", default="owner", choices=ROLES, help="default: owner")
    parser.add_argument(
        "--password",
        default=None,
        help=f"optional; if omitted a {20}-character password is generated",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    settings = get_settings()
    configure_logging(settings.log_level, json_output=settings.log_json)

    try:
        user, plaintext = create_user(
            settings,
            email=args.email,
            role=args.role,
            password=args.password,
        )
    except CreateUserError as exc:
        logger.error("create_user: %s", exc)
        return 1
    except SQLAlchemyError as exc:
        logger.warning("create_user: database unavailable (%s)", exc)
        return 1

    # The password is printed once, never logged.
    print(f"created user {user.email} ({user.role}) id={user.id}")
    print(f"password: {plaintext}")
    print("store it now; it is not recoverable from the database")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
