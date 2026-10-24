"""Field-level encryption for restricted data (passport, DOB, contact values).

Ciphertext format: ``enc:v1:<fernet-token>``. The key comes from a
``KeyProvider``; production uses ``ENCRYPTION_KEY`` and tests inject a static
provider. Ciphertext is never logged (the redaction filter masks it).
"""

from __future__ import annotations

import base64
import os
from datetime import date
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from travelos.core.config import get_settings
from travelos.core.errors import EncryptionError, EncryptionKeyMissingError

ENCRYPTED_PREFIX = "enc:v1:"
_KEY_BYTES = 32


class KeyProvider(Protocol):
    """Supplies the active 32-byte encryption key."""

    def get_key(self) -> bytes:
        """Return the raw 32-byte key. Raise if unavailable."""
        ...


class StaticKeyProvider:
    """Test/dev provider with an in-memory key."""

    def __init__(self, key: bytes) -> None:
        if len(key) != _KEY_BYTES:
            raise EncryptionError(f"encryption key must be {_KEY_BYTES} bytes")
        self._key = key

    def get_key(self) -> bytes:
        return self._key


class SettingsKeyProvider:
    """Production provider backed by ``ENCRYPTION_KEY`` in settings/env."""

    def get_key(self) -> bytes:
        encoded = get_settings().encryption_key
        if encoded is None:
            raise EncryptionKeyMissingError(
                "ENCRYPTION_KEY is not set; generate one with "
                '`python -c "import base64,os;print(base64.urlsafe_b64encode(os.urandom(32)).decode())"`'
            )
        try:
            key = base64.urlsafe_b64decode(encoded.get_secret_value().encode("ascii"))
        except (ValueError, UnicodeEncodeError) as exc:
            raise EncryptionError("ENCRYPTION_KEY is not valid base64") from exc
        if len(key) != _KEY_BYTES:
            raise EncryptionError(f"ENCRYPTION_KEY must decode to {_KEY_BYTES} bytes")
        return key


def generate_key() -> str:
    """Generate a base64 key suitable for ``ENCRYPTION_KEY``."""

    return base64.urlsafe_b64encode(os.urandom(_KEY_BYTES)).decode("ascii")


class EncryptionService:
    """Authenticated symmetric encryption with a versioned ciphertext prefix."""

    def __init__(self, provider: KeyProvider) -> None:
        self._provider = provider
        self._fernet = Fernet(base64.urlsafe_b64encode(provider.get_key()))

    def encrypt(self, plaintext: str) -> str:
        token = self._fernet.encrypt(plaintext.encode("utf-8")).decode("ascii")
        return f"{ENCRYPTED_PREFIX}{token}"

    def decrypt(self, ciphertext: str) -> str:
        if not ciphertext.startswith(ENCRYPTED_PREFIX):
            raise EncryptionError("value is not TravelOS ciphertext")
        token = ciphertext.removeprefix(ENCRYPTED_PREFIX)
        try:
            return self._fernet.decrypt(token.encode("ascii")).decode("utf-8")
        except (InvalidToken, UnicodeDecodeError, UnicodeEncodeError) as exc:
            raise EncryptionError("ciphertext could not be decrypted") from exc


_default_provider: KeyProvider | None = None


def default_key_provider() -> KeyProvider:
    """Return the process key provider, defaulting to settings/env."""

    global _default_provider
    if _default_provider is None:
        _default_provider = SettingsKeyProvider()
    return _default_provider


def set_key_provider(provider: KeyProvider | None) -> None:
    """Override the provider (tests and key rotation tooling)."""

    global _default_provider
    _default_provider = provider


def _service() -> EncryptionService:
    return EncryptionService(default_key_provider())


class EncryptedString(TypeDecorator[str]):
    """Transparently encrypts a string column at rest."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            raise EncryptionError("encrypted column requires a string")
        return _service().encrypt(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        return _service().decrypt(value)


class EncryptedDate(TypeDecorator[date]):
    """Encrypts a date as an ISO-8601 string (e.g. DOB, passport expiry)."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: date | None, dialect: Dialect) -> str | None:
        if value is None:
            return None
        if not isinstance(value, date):
            raise EncryptionError("encrypted date column requires a date")
        return _service().encrypt(value.isoformat())

    def process_result_value(self, value: str | None, dialect: Dialect) -> date | None:
        if value is None:
            return None
        try:
            return date.fromisoformat(_service().decrypt(value))
        except ValueError as exc:
            raise EncryptionError("decrypted value is not an ISO date") from exc
