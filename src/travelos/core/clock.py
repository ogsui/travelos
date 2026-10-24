"""Injectable clocks so timers, retries and tests never depend on wall time."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol


class Clock(Protocol):
    """The only source of 'now' in scheduling code."""

    def now(self) -> datetime:
        """Current time, timezone-aware and in UTC."""
        ...


@dataclass(frozen=True, slots=True)
class SystemClock:
    """Production clock."""

    def now(self) -> datetime:
        return datetime.now(UTC)


class ManualClock:
    """Test/debug clock that only moves when a test moves it."""

    def __init__(self, now: datetime | None = None) -> None:
        self._now = now or datetime.now(UTC)
        if self._now.tzinfo is None:
            raise ValueError("clock time must be timezone-aware")

    def now(self) -> datetime:
        return self._now

    def set(self, value: datetime) -> None:
        if value.tzinfo is None:
            raise ValueError("clock time must be timezone-aware")
        self._now = value

    def advance(self, delta: timedelta) -> None:
        self._now += delta
