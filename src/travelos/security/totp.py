"""TOTP (RFC 6238) for owner/admin two-factor authentication.

Implemented locally so the verification path is small, dependency-free and
testable against the RFC vectors. SHA-1, 30-second period, 6 digits.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import time
from urllib.parse import quote, urlencode

DEFAULT_DIGITS = 6
DEFAULT_PERIOD = 30
DEFAULT_WINDOW = 1
_SECRET_BYTES = 20
_MAX_WINDOW = 5


def generate_secret() -> str:
    """Return a new base32 secret without padding (as authenticator apps expect)."""

    return base64.b32encode(secrets.token_bytes(_SECRET_BYTES)).decode("ascii").rstrip("=")


def _decode_secret(secret: str) -> bytes:
    normalized = secret.strip().replace(" ", "").upper()
    padded = normalized + "=" * (-len(normalized) % 8)
    return base64.b32decode(padded, casefold=True)


def _code_at(secret: str, counter: int, digits: int) -> str:
    digest = hmac.new(_decode_secret(secret), counter.to_bytes(8, "big"), hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    binary = int.from_bytes(digest[offset : offset + 4], "big") & 0x7FFFFFFF
    return str(binary % (10**digits)).zfill(digits)


def generate_code(
    secret: str,
    *,
    at: float | None = None,
    digits: int = DEFAULT_DIGITS,
    period: int = DEFAULT_PERIOD,
) -> str:
    """Return the code valid at ``at`` (default: now)."""

    counter = int((time.time() if at is None else at) // period)
    return _code_at(secret, counter, digits)


def verify_code(
    secret: str,
    code: str,
    *,
    at: float | None = None,
    digits: int = DEFAULT_DIGITS,
    period: int = DEFAULT_PERIOD,
    window: int = DEFAULT_WINDOW,
) -> bool:
    """Verify a code allowing ``window`` periods of clock drift.

    All comparisons run, and use ``compare_digest``, so timing does not reveal
    which candidate matched.
    """

    if not isinstance(code, str) or not code.isdigit() or len(code) != digits:
        return False
    if not 0 <= window <= _MAX_WINDOW:
        raise ValueError(f"window must be between 0 and {_MAX_WINDOW}")
    counter = int((time.time() if at is None else at) // period)
    matched = False
    for offset in range(-window, window + 1):
        candidate = counter + offset
        if candidate < 0:
            # No codes exist before the Unix epoch.
            continue
        expected = _code_at(secret, candidate, digits)
        matched |= hmac.compare_digest(expected, code)
    return matched


def provisioning_uri(
    secret: str,
    account_name: str,
    *,
    issuer: str = "TravelOS",
    digits: int = DEFAULT_DIGITS,
    period: int = DEFAULT_PERIOD,
) -> str:
    """Return an ``otpauth://`` URI for authenticator apps."""

    label = quote(f"{issuer}:{account_name}", safe="")
    query = urlencode(
        {
            "secret": secret,
            "issuer": issuer,
            "algorithm": "SHA1",
            "digits": digits,
            "period": period,
        }
    )
    return f"otpauth://totp/{label}?{query}"
