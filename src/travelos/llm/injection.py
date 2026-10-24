"""Defences against prompt injection in untrusted text.

Customer messages, scraped pages, supplier emails and uploaded files are data.
They are never concatenated into instructions. These helpers:

* neutralise chat-template control tokens and our own delimiter,
* remove a conservative set of instruction-like lines,
* wrap the remaining text in a delimited DATA block, and
* enforce an optional allow-list on structured output.

No helper decides whether an action is safe. Actions still go through the
approval engine, and the provider request type has no tool/function field.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

UNTRUSTED_OPEN = "<<<UNTRUSTED_DATA>>>"
UNTRUSTED_CLOSE = "<<<END_UNTRUSTED_DATA>>>"
_INSTRUCTION_MARKERS = (
    re.compile(
        r"(?i)^\s*(?:ignore|disregard|forget|override)\b.*"
        r"\b(?:instructions?|prompts?|directions?|rules?)\b.*$"
    ),
    re.compile(r"(?i)^\s*(?:system|developer)\s*(?:prompt|message)\s*[:=].*$"),
    re.compile(r"(?i)^\s*you\s+are\s+(?:now|no\s+longer)\b.*$"),
    re.compile(
        r"(?i)^\s*(?:reveal|print|show|repeat|leak)\b.*"
        r"\b(?:system\s+prompt|instructions?|hidden\s+prompt)\b.*$"
    ),
    re.compile(r"(?i)^\s*(?:call|invoke|execute|use)\b.*\b(?:tool|function|plugin)\b.*$"),
    re.compile(r"(?i)^\s*(?:assistant|system)\s*:\s*.*$"),
)
_CONTROL_TOKENS = (
    "<|im_start|>",
    "<|im_end|>",
    "<|system|>",
    "<|user|>",
    "<|assistant|>",
    "[INST]",
    "[/INST]",
    "<<SYS>>",
    "<</SYS>>",
)
_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_REMOVED = "[removed: instruction-like text]"
_MAX_UNTRUSTED_CHARS = 20_000


def sanitise_untrusted(text: str, *, max_chars: int = _MAX_UNTRUSTED_CHARS) -> str:
    """Make untrusted text safe to place inside a prompt as DATA."""

    if not text:
        return ""
    cleaned = _CONTROL_CHARS.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n"))
    for token in _CONTROL_TOKENS:
        cleaned = cleaned.replace(token, " ")
    # A message must not be able to close our delimiter early.
    cleaned = cleaned.replace("<<<", "\u2039\u2039\u2039").replace(">>>", "\u203a\u203a\u203a")
    lines = []
    for line in cleaned.split("\n"):
        lines.append(_REMOVED if any(p.match(line) for p in _INSTRUCTION_MARKERS) else line)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{4,}", "\n\n\n", cleaned).strip()
    if max_chars > 0 and len(cleaned) > max_chars:
        cleaned = cleaned[:max_chars].rstrip() + "\n[truncated]"
    return cleaned


def delimit_untrusted(text: str, *, label: str = "data") -> str:
    """Wrap sanitised untrusted text in an explicit DATA block."""

    safe_label = re.sub(r"[^a-zA-Z0-9_-]+", "_", label).strip("_") or "data"
    body = sanitise_untrusted(text)
    return f'<<<UNTRUSTED_DATA name="{safe_label}">>>\n{body}\n<<<END_UNTRUSTED_DATA>>>'


def contains_injection(text: str) -> bool:
    """Best-effort detector used by tests and dashboards, never for decisions."""

    if any(token in text for token in _CONTROL_TOKENS):
        return True
    search = re.compile(
        r"(?i)\b(?:ignore|disregard|forget|override)\b[^\n]{0,80}"
        r"\b(?:instructions?|prompts?|directions?|rules?)\b"
    )
    if search.search(text):
        return True
    return any(marker.match(line) for line in text.splitlines() for marker in _INSTRUCTION_MARKERS)


def check_allow_list(
    value: object, allowed: Mapping[str, Sequence[str]], *, path: str = "$"
) -> list[str]:
    """Check top-level string fields against per-field allowed values."""

    errors: list[str] = []
    if not isinstance(value, dict):
        return [f"{path}: an object is required to apply the output allow-list"]
    for field_name, permitted in allowed.items():
        if field_name not in value:
            continue
        candidate = value[field_name]
        if not isinstance(candidate, str) or candidate not in permitted:
            errors.append(f"{path}.{field_name}: {candidate!r} is not one of {sorted(permitted)!r}")
    return errors
