"""Reversible PII redaction for prompts.

Log redaction (``travelos.core.redaction``) is deliberately irreversible: once a
value is in a log line it is gone. LLM prompts need the opposite. We replace
personal data with typed placeholders (``[[EMAIL_1]]``), send only the
placeholders to the provider, and restore the original values in the model's
answer. The mapping lives in this process for the duration of one call and is
never written to the database or logs.

This is defence in depth, not a guarantee: no regex knows every name or address.
Callers can mark prompt variables as ``sensitive`` so a value is always
placeholder-bound even when no pattern matches.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from travelos.core.redaction import REDACTED

_EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
_PASSPORT = re.compile(r"\b[A-PR-WY][1-9]\d{6}\b")
_CARD = re.compile(r"\b(?:\d{4}[ -]?){3}\d{4}\b")
_AADHAAR = re.compile(r"\b\d{4}\s?\d{4}\s?\d{4}\b")
_PHONE = re.compile(r"(?<!\d)(?:\+?91[\s-]?)?[6-9]\d{4}[\s-]?\d{5}(?!\d)")
_INTERNATIONAL_PHONE = re.compile(r"\+\d{1,3}[\s-]?\d{6,14}\b")
_CIPHERTEXT = re.compile(r"enc:v1:[A-Za-z0-9_=.-]+")
_JWT = re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b")
_CREDENTIAL = re.compile(
    r"(?i)\b(api[_-]?key|authorization|password|passwd|secret|token)\b"
    r"\s*[:=]\s*(?:bearer\s+)?[^\s,;]+"
)
_DOB_LABEL = re.compile(
    r"(?i)\b(passport(?:\s*(?:no|number))?|date\s*of\s*birth|dob)\b\s*[:=]\s*\S+"
)

# Order matters. Cards look like Aadhaar/phone numbers; passports look like
# words. Match the most specific pattern first.
_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("CIPHERTEXT", _CIPHERTEXT),
    ("JWT", _JWT),
    ("CREDENTIAL", _CREDENTIAL),
    ("EMAIL", _EMAIL),
    ("CARD", _CARD),
    ("PHONE", _INTERNATIONAL_PHONE),
    ("AADHAAR", _AADHAAR),
    ("PASSPORT", _PASSPORT),
    ("PHONE", _PHONE),
    ("ID", _DOB_LABEL),
)
_PLACEHOLDER = re.compile(r"\[\[([A-Z][A-Z0-9_]*)_(\d+)\]\]")


@dataclass(frozen=True, slots=True)
class RedactionResult:
    """The redacted text and the placeholders that were used."""

    text: str
    placeholders: tuple[str, ...] = ()


@dataclass(slots=True)
class RedactionSession:
    """Per-call, server-side mapping between placeholders and originals."""

    _values: dict[str, str] = field(default_factory=dict)
    _reverse: dict[str, str] = field(default_factory=dict)
    _counters: dict[str, int] = field(default_factory=dict)

    def redact(self, text: str) -> str:
        """Replace detected PII with stable placeholders.

        The same original value always receives the same placeholder inside one
        session, so the model can reason about "the same email" without seeing
        it twice. Placeholder-looking text supplied by the user is escaped so a
        later restore cannot be tricked into substituting it.
        """

        if not text:
            return text
        protected = text.replace("[[", "[\u200b[")
        for label, pattern in _PATTERNS:

            def replace(match: re.Match[str], _label: str = label) -> str:
                return self._placeholder(_label, match.group(0))

            protected = pattern.sub(replace, protected)
        return protected

    def redact_value(self, value: str, *, label: str = "VALUE") -> str:
        """Always placeholder-bind a whole value, even if no pattern matches.

        Used for prompt variables the prompt author marked ``sensitive`` (a
        customer's name, for example).
        """

        if not value:
            return value
        return self._placeholder(_safe_label(label), value)

    def restore(self, text: str) -> str:
        """Put original values back into model output."""

        if not text:
            return text
        return _PLACEHOLDER.sub(lambda match: self._lookup(match.group(0)), text)

    def restore_value(self, value: Any) -> Any:
        """Restore placeholders recursively inside JSON-compatible data."""

        if isinstance(value, str):
            return self.restore(value)
        if isinstance(value, Mapping):
            return {self.restore(str(key)): self.restore_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.restore_value(item) for item in value]
        if isinstance(value, tuple):
            return tuple(self.restore_value(item) for item in value)
        return value

    @property
    def placeholders(self) -> tuple[str, ...]:
        return tuple(self._values)

    @property
    def mapping(self) -> Mapping[str, str]:
        """Read-only view for tests and for a caller that must restore later.

        Callers must never persist or log this mapping.
        """

        return dict(self._values)

    def clear(self) -> None:
        self._values.clear()
        self._reverse.clear()
        self._counters.clear()

    def redacted_log_value(self) -> str:
        """A safe description of what was redacted, for logs and audit."""

        if not self._values:
            return "none"
        counts: dict[str, int] = {}
        for placeholder in self._values:
            label = placeholder[2:].rsplit("_", 1)[0]
            counts[label] = counts.get(label, 0) + 1
        return ", ".join(f"{label}={count}" for label, count in sorted(counts.items()))

    def _placeholder(self, label: str, original: str) -> str:
        if original in self._reverse:
            return self._reverse[original]
        next_index = self._counters.get(label, 0) + 1
        placeholder = f"[[{label}_{next_index}]]"
        while placeholder in self._values:
            next_index += 1
            placeholder = f"[[{label}_{next_index}]]"
        self._counters[label] = next_index
        self._reverse[original] = placeholder
        self._values[placeholder] = original
        return placeholder

    def _lookup(self, placeholder: str) -> str:
        return self._values.get(placeholder, REDACTED)


def _safe_label(label: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9]+", "_", label).strip("_").upper()
    return cleaned or "VALUE"
