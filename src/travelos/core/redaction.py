"""PII and secret redaction for logs and LLM payloads.

The regexes are intentionally conservative. It is better to over-redact a log
line than to leak a phone number, passport number or API token.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from typing import Any

from pydantic import SecretStr

REDACTED = "[REDACTED]"

_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # TravelOS field-level ciphertext first: it is opaque, so mask it before
    # any other pattern can nibble at the base64 body.
    (re.compile(r"enc:v1:[A-Za-z0-9_=.-]+"), "[REDACTED_CIPHERTEXT]"),
    # JWT / bearer-ish tokens
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        "[REDACTED_JWT]",
    ),
    # key=value / key: value for credentials
    (
        re.compile(
            r"(?i)\b(api[_-]?key|authorization|password|passwd|secret|token)\b"
            r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
        ),
        r"\1=" + REDACTED,
    ),
    # Email addresses
    (
        re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"),
        "[REDACTED_EMAIL]",
    ),
    # Indian passport numbers (letter followed by seven digits)
    (re.compile(r"\b[A-PR-WY][1-9]\d{6}\b"), "[REDACTED_PASSPORT]"),
    # Aadhaar-like 12 digit identifiers
    (re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b"), "[REDACTED_ID]"),
    # Payment card numbers, grouped or contiguous
    (re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b"), "[REDACTED_CARD]"),
    # Indian mobile numbers and international numbers
    (re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)"), "[REDACTED_PHONE]"),
    (re.compile(r"\+\d{1,3}[\s-]?\d{6,14}\b"), "[REDACTED_PHONE]"),
    # Passport/date-of-birth labelled values
    (
        re.compile(r"(?i)\b(passport(?:\s*(?:no|number))?|date\s*of\s*birth|dob)\b\s*[:=]\s*\S+"),
        r"\1=" + REDACTED,
    ),
)

_SENSITIVE_KEYS = frozenset(
    {
        "password",
        "passwd",
        "secret",
        "client_secret",
        "token",
        "access_token",
        "refresh_token",
        "authorization",
        "cookie",
        "set_cookie",
        "passport",
        "passport_no",
        "passport_number",
        "dob",
        "date_of_birth",
        "email",
        "phone",
        "mobile",
        "phone_number",
        "card",
        "card_number",
        "cvv",
        "cvc",
        "pan",
        "aadhaar",
        "api_key",
        "apikey",
        "secret_key",
        "private_key",
        "access_key",
    }
)
_SENSITIVE_PARTS = frozenset(
    {"password", "passwd", "secret", "token", "passport", "cvv", "aadhaar", "pan"}
)


def redact_text(value: str) -> str:
    """Return ``value`` with detected PII and credentials replaced."""

    redacted = value
    for pattern, replacement in _PATTERNS:
        redacted = pattern.sub(replacement, redacted)
    return redacted


def is_sensitive_key(key: str) -> bool:
    """Whether a mapping key names a credential or direct identifier."""

    normalized = re.sub(r"[^a-z0-9]+", "_", key.lower()).strip("_")
    if normalized in _SENSITIVE_KEYS:
        return True
    return bool(set(normalized.split("_")) & _SENSITIVE_PARTS)


def redact_mapping(value: Mapping[str, Any]) -> dict[str, Any]:
    """Recursively redact a mapping (used for log extras and payloads)."""

    result: dict[str, Any] = {}
    for key, item in value.items():
        if is_sensitive_key(key):
            result[key] = REDACTED
        else:
            result[key] = _redact_value(item)
    return result


def _redact_value(item: Any) -> Any:
    if isinstance(item, SecretStr):
        return REDACTED
    if isinstance(item, str):
        return redact_text(item)
    if isinstance(item, Mapping):
        return redact_mapping(item)
    if isinstance(item, list | tuple | set | frozenset):
        return type(item)(_redact_value(element) for element in item)
    return item


class RedactionFilter:
    """Logging filter that redacts values of sensitive ``extra`` fields.

    The JSON formatter redacts the final rendered message as well, so both
    structured context and free-text messages are covered.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        for key, value in list(record.__dict__.items()):
            if key.startswith("_") or key in LOG_RECORD_ATTRIBUTES:
                continue
            if is_sensitive_key(key):
                record.__dict__[key] = REDACTED
            else:
                record.__dict__[key] = _redact_value(value)
        return True


# Standard LogRecord attributes; extras are everything outside this set.
LOG_RECORD_ATTRIBUTES = frozenset(
    {
        "args",
        "asctime",
        "created",
        "exc_info",
        "exc_text",
        "filename",
        "funcName",
        "levelname",
        "levelno",
        "lineno",
        "module",
        "msecs",
        "message",
        "msg",
        "name",
        "pathname",
        "process",
        "processName",
        "relativeCreated",
        "stack_info",
        "taskName",
        "thread",
        "threadName",
    }
)
