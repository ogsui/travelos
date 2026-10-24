"""Development response cache for repeated, deterministic LLM calls.

Only successful, schema-validated responses are cached. Entries contain the
redacted response text (placeholders, not personal data) and expire quickly.
The cache is process-local and is automatically disabled in live mode by the
factory; it exists to keep local development and evals cheap, not to serve
production traffic.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from travelos.core.clock import Clock, SystemClock


@dataclass(frozen=True, slots=True)
class CachedResponse:
    provider: str
    model: str
    text: str
    parsed: object | None
    input_tokens: int
    output_tokens: int
    repairs: int
    currency: str = "INR"


class ResponseCache:
    """Bounded LRU cache with a wall-clock TTL."""

    def __init__(
        self,
        *,
        ttl: timedelta = timedelta(minutes=5),
        max_entries: int = 256,
        clock: Clock | None = None,
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("cache ttl must be positive")
        if max_entries < 1:
            raise ValueError("max_entries must be positive")
        self._ttl = ttl
        self._max_entries = max_entries
        self._clock = clock or SystemClock()
        self._entries: OrderedDict[str, tuple[datetime, CachedResponse]] = OrderedDict()

    def key(self, *parts: object) -> str:
        payload = json.dumps(parts, sort_keys=True, separators=(",", ":"), default=str)
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def get(self, key: str) -> CachedResponse | None:
        entry = self._entries.get(key)
        if entry is None:
            return None
        expires_at, value = entry
        if self._clock.now() >= expires_at:
            self._entries.pop(key, None)
            return None
        self._entries.move_to_end(key)
        return value

    def put(self, key: str, value: CachedResponse) -> None:
        self._entries[key] = (self._clock.now() + self._ttl, value)
        self._entries.move_to_end(key)
        while len(self._entries) > self._max_entries:
            self._entries.popitem(last=False)

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)


def canonical(value: Any) -> str:
    """Stable JSON used in hashes and cache keys."""

    return json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
