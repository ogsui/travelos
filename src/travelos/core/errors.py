"""Typed domain errors shared across modules."""

from __future__ import annotations


class TravelOSError(Exception):
    """Base class for expected application errors."""


class ValidationError(TravelOSError):
    """Input violates a domain rule."""


class CurrencyMismatchError(ValidationError):
    """Arithmetic attempted across two currencies."""


class EncryptionError(TravelOSError):
    """Ciphertext could not be decrypted or a key is unavailable."""


class EncryptionKeyMissingError(EncryptionError):
    """No encryption key provider is configured."""


class AppendOnlyError(TravelOSError):
    """An append-only table was modified or deleted."""
