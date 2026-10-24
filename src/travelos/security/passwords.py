"""Argon2id password hashing.

Only hashes leave this module. Plaintext passwords are never stored, logged or
audited, and verification uses argon2's constant-time comparison.
"""

from __future__ import annotations

from functools import lru_cache

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_PASSWORD_LENGTH = 12

_hasher = PasswordHasher()


def hash_password(password: str) -> str:
    """Hash a password with argon2id and a per-password salt."""

    if not isinstance(password, str) or not password:
        raise ValueError("password must be a non-empty string")
    return _hasher.hash(password)


def verify_password(password: str, password_hash: str) -> bool:
    """Constant-time check of a password against a stored hash."""

    try:
        return _hasher.verify(password_hash, password)
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    """Whether a stored hash uses weaker parameters than the current policy."""

    try:
        return _hasher.check_needs_rehash(password_hash)
    except InvalidHashError:
        return True


@lru_cache(maxsize=1)
def _dummy_hash() -> str:
    return hash_password("timing-equalisation-placeholder")


def verify_password_or_dummy(password: str, password_hash: str | None) -> bool:
    """Verify, but still spend argon2 time when the user does not exist.

    This keeps the unknown-email and wrong-password paths similar in latency,
    so login cannot be used to enumerate accounts.
    """

    if password_hash is None:
        verify_password(password, _dummy_hash())
        return False
    return verify_password(password, password_hash)
