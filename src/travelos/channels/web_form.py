"""Public web-form validation, privacy-safe rate limiting, and intake."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from redis import Redis
from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.channels.models import Conversation, Message
from travelos.channels.service import ChannelService, InboundMessage, InboundResult
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.crm.fingerprints import ContactFingerprinter

WEB_FORM_LIMIT = 5
WEB_FORM_WINDOW_SECONDS = 600


class RateLimitStore(Protocol):
    def allow(self, source_digest: str) -> bool: ...


class InMemoryWindowLimiter:
    """Small deterministic limiter for test mode; production uses Redis."""

    def __init__(
        self,
        *,
        limit: int = WEB_FORM_LIMIT,
        window_seconds: int = WEB_FORM_WINDOW_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if limit < 1 or window_seconds < 1:
            raise ValueError("rate limit and window must be positive")
        self._limit = limit
        self._window_seconds = window_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[float, int]] = {}
        self._checks = 0

    def allow(self, source_digest: str) -> bool:
        now = self._clock()
        with self._lock:
            self._checks += 1
            if self._checks % 128 == 0:
                self._windows = {
                    key: value for key, value in self._windows.items() if value[0] > now
                }
            expires_at, count = self._windows.get(source_digest, (0.0, 0))
            if expires_at <= now:
                expires_at, count = now + self._window_seconds, 0
            if count >= self._limit:
                self._windows[source_digest] = (expires_at, count)
                return False
            self._windows[source_digest] = (expires_at, count + 1)
            return True


class RedisWindowLimiter:
    """Shared atomic fixed-window limiter for shadow/live API instances."""

    _SCRIPT = """
local count = redis.call('INCR', KEYS[1])
if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
return count
"""

    def __init__(
        self,
        client: Redis[bytes],
        *,
        limit: int = WEB_FORM_LIMIT,
        window_seconds: int = WEB_FORM_WINDOW_SECONDS,
    ) -> None:
        self._client = client
        self._limit = limit
        self._window_seconds = window_seconds

    @classmethod
    def from_url(cls, url: str) -> RedisWindowLimiter:
        return cls(
            Redis.from_url(
                url,
                decode_responses=False,
                socket_connect_timeout=1,
                socket_timeout=1,
            )
        )

    def allow(self, source_digest: str) -> bool:
        key = f"travelos:web-form:rate:v1:{source_digest}"
        # redis-py leaves eval() untyped; the Lua script returns an integer.
        count = self._client.eval(  # type: ignore[no-untyped-call]
            self._SCRIPT, 1, key, self._window_seconds
        )
        return int(count) <= self._limit

    def close(self) -> None:
        self._client.close()


class WebFormRateLimitUnavailable(RuntimeError):
    """Shared limiter is unavailable; request must fail closed."""


class WebFormRateLimited(RuntimeError):
    """Public source exceeded its intake rate limit."""


class WebFormService:
    def __init__(
        self,
        limiter: RateLimitStore,
        *,
        fingerprinter: ContactFingerprinter | None = None,
        channels: ChannelService | None = None,
    ) -> None:
        self._limiter = limiter
        self._fingerprinter = fingerprinter or ContactFingerprinter()
        self._channels = channels or ChannelService()

    def submit(
        self,
        db: Session,
        *,
        submission_id: UUID,
        message: str,
        source_ip: str,
        tenant_id: UUID = DEFAULT_TENANT_ID,
    ) -> InboundResult:
        external_id = f"web-form:{submission_id}"
        existing = db.execute(
            select(Message).where(
                Message.tenant_id == tenant_id,
                Message.channel == "web_form",
                Message.external_id == external_id,
            )
        ).scalar_one_or_none()
        if existing is not None:
            conversation = db.get(Conversation, existing.conversation_id)
            if conversation is None:
                raise RuntimeError("web-form message has no conversation")
            return InboundResult(message=existing, conversation=conversation, duplicate=True)

        digest = self._fingerprinter.keyed_digest("web-form-source-ip", source_ip.encode("utf-8"))
        try:
            allowed = self._limiter.allow(digest)
        except Exception as exc:
            raise WebFormRateLimitUnavailable from exc
        if not allowed:
            raise WebFormRateLimited
        now = datetime.now(UTC)
        return self._channels.receive(
            db,
            event=InboundMessage(
                channel="web_form",
                external_id=external_id,
                external_thread_id=external_id,
                body=message,
                occurred_at=now,
            ),
            tenant_id=tenant_id,
        )
