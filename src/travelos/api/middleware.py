"""Request context and response hardening middleware."""

from __future__ import annotations

import logging
import re
import time
import uuid

from fastapi import Request
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from travelos.core.logging import reset_request_id, set_request_id

logger = logging.getLogger("travelos.http")

_REQUEST_ID_PATTERN = re.compile(r"^[A-Za-z0-9._-]{8,64}$")
_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
}


def _request_id(request: Request) -> str:
    candidate = request.headers.get("X-Request-ID", "")
    return candidate if _REQUEST_ID_PATTERN.fullmatch(candidate) else uuid.uuid4().hex


class RequestContextMiddleware:
    """Attach a request ID, log the request, and add security headers.

    Implemented as plain ASGI middleware to avoid the streaming and exception
    caveats of ``BaseHTTPMiddleware``.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request = Request(scope)
        request_id = _request_id(request)
        request.state.request_id = request_id
        token = set_request_id(request_id)
        started = time.perf_counter()
        method = request.method
        path = request.url.path
        status_code = 500

        async def send_with_headers(message: Message) -> None:
            nonlocal status_code
            if message["type"] == "http.response.start":
                status_code = message["status"]
                headers = list(message.get("headers", []))
                headers.append((b"x-request-id", request_id.encode("ascii")))
                for name, value in _SECURITY_HEADERS.items():
                    headers.append((name.lower().encode("ascii"), value.encode("ascii")))
                message["headers"] = headers
            await send(message)

        try:
            await self.app(scope, receive, send_with_headers)
        except Exception:
            logger.exception(
                "request failed",
                extra={
                    "method": method,
                    "path": path,
                    "duration_ms": _elapsed_ms(started),
                },
            )
            raise
        finally:
            logger.info(
                "request completed",
                extra={
                    "method": method,
                    "path": path,
                    "status_code": status_code,
                    "duration_ms": _elapsed_ms(started),
                },
            )
            reset_request_id(token)


def _elapsed_ms(started: float) -> float:
    return round((time.perf_counter() - started) * 1000, 2)
