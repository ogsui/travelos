"""Normalized, keyed contact fingerprints for dedupe and suppression lookups."""

from __future__ import annotations

import hashlib
import hmac
import re

from travelos.security.encryption import KeyProvider, default_key_provider

_KEY_CONTEXT = b"travelos/contact-fingerprint/v1"
_PHONE_SEPARATORS = re.compile(r"[\s().-]+")


class ContactFingerprinter:
    """Create channel-scoped HMAC-SHA256 digests without storing contact values."""

    def __init__(self, provider: KeyProvider | None = None) -> None:
        self._provider = provider or default_key_provider()

    def fingerprint(self, channel: str, value: str) -> str:
        normalized_channel = channel.strip().lower()
        if not normalized_channel:
            raise ValueError("contact channel is required")
        normalized_value = normalize_contact_value(normalized_channel, value)
        message = f"{normalized_channel}\0{normalized_value}".encode()
        root_key = self._provider.get_key()
        key = hmac.new(root_key, _KEY_CONTEXT, hashlib.sha256).digest()
        return hmac.new(key, message, hashlib.sha256).hexdigest()

    def keyed_digest(self, purpose: str, value: bytes) -> str:
        """Return an opaque digest with a purpose-specific, domain-separated key."""

        if not purpose.strip():
            raise ValueError("digest purpose is required")
        root_key = self._provider.get_key()
        key = hmac.new(root_key, _KEY_CONTEXT, hashlib.sha256).digest()
        message = f"{purpose}\0".encode() + value
        return hmac.new(key, message, hashlib.sha256).hexdigest()


def normalize_contact_value(channel: str, value: str) -> str:
    """Canonicalize equivalent email, phone and other supported channel values."""

    normalized = value.strip()
    if not normalized:
        raise ValueError("contact value is required")
    if channel in {"phone", "sms", "whatsapp"}:
        normalized = _PHONE_SEPARATORS.sub("", normalized)
        if normalized.startswith("00"):
            normalized = "+" + normalized[2:]
        if normalized.startswith("+"):
            digits = normalized[1:]
            if not digits.isdigit():
                raise ValueError("phone contact contains unsupported characters")
            return "+" + digits
        if not normalized.isdigit():
            raise ValueError("phone contact contains unsupported characters")
        return normalized
    # Email addresses and account handles are case-insensitive for matching.
    # Other channel values use the same conservative whitespace/case folding.
    return normalized.casefold()
