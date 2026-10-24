"""Structured JSON logging with request IDs and PII redaction."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar, Token
from datetime import UTC, datetime
from typing import Any

from travelos.core.redaction import (
    LOG_RECORD_ATTRIBUTES,
    RedactionFilter,
    redact_mapping,
    redact_text,
)

request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)


def get_request_id() -> str | None:
    """Return the request ID for the current context, if any."""

    return request_id_var.get()


def set_request_id(request_id: str) -> Token[str | None]:
    """Bind a request ID to the current context."""

    return request_id_var.set(request_id)


def reset_request_id(token: Token[str | None]) -> None:
    """Restore the previous request ID context."""

    request_id_var.reset(token)


class JsonFormatter(logging.Formatter):
    """Render one JSON object per log line, with redaction applied last."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": get_request_id(),
        }

        for key, value in record.__dict__.items():
            if key in LOG_RECORD_ATTRIBUTES or key.startswith("_"):
                continue
            payload[key] = value

        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        if record.stack_info:
            payload["stack"] = self.formatStack(record.stack_info)

        safe_payload = redact_mapping(payload)
        return json.dumps(safe_payload, ensure_ascii=False, default=str)


class TextFormatter(logging.Formatter):
    """Human-readable formatter for local development, still redacted."""

    def format(self, record: logging.LogRecord) -> str:
        request_id = get_request_id()
        prefix = f"[{request_id[:8]}] " if request_id else ""
        message = f"{prefix}{record.levelname} {record.name}: {record.getMessage()}"
        if record.exc_info:
            message += "\n" + self.formatException(record.exc_info)
        return redact_text(message)


def configure_logging(level: str = "INFO", *, json_output: bool = True) -> None:
    """Configure root logging once per process.

    Existing handlers owned by other tools (pytest, uvicorn) are left alone;
    only a previous TravelOS handler is replaced.
    """

    numeric_level = logging.getLevelNamesMapping().get(level.upper())
    if numeric_level is None:
        raise ValueError(f"Unknown log level: {level!r}")

    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "_travelos_handler", False):
            root.removeHandler(handler)

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter() if json_output else TextFormatter())
    handler.addFilter(RedactionFilter())
    handler._travelos_handler = True  # type: ignore[attr-defined]
    root.addHandler(handler)
    root.setLevel(numeric_level)

    # Uvicorn installs its own handlers; make its records use ours instead.
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        logger = logging.getLogger(name)
        logger.handlers.clear()
        logger.propagate = True
