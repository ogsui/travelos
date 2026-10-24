"""Per-provider circuit breaker.

A provider that keeps timing out should not slow every request in the chain.
After ``failure_threshold`` consecutive transport failures the breaker opens for
``reset_after``; one successful probe closes it again. Schema failures do not
count: the provider was reachable and a repair may fix the output.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from travelos.core.clock import Clock, SystemClock


@dataclass
class CircuitBreaker:
    failure_threshold: int = 3
    reset_after: timedelta = timedelta(seconds=60)
    clock: Clock | None = None

    def __post_init__(self) -> None:
        if self.failure_threshold < 1:
            raise ValueError("failure_threshold must be >= 1")
        if self.reset_after <= timedelta(0):
            raise ValueError("reset_after must be positive")
        self._clock = self.clock or SystemClock()
        self._failures = 0
        self._opened_at: datetime | None = None

    def allows(self) -> bool:
        if self._opened_at is None:
            return True
        if self._clock.now() - self._opened_at >= self.reset_after:
            # Half-open: let one request probe the provider.
            self._opened_at = None
            self._failures = 0
            return True
        return False

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._opened_at = self._clock.now()

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        if self._clock.now() - self._opened_at >= self.reset_after:
            return "half_open"
        return "open"

    @property
    def failures(self) -> int:
        return self._failures
