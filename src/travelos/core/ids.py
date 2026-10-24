"""Time-sortable UUIDv7 identifiers (RFC 9562)."""

from __future__ import annotations

import secrets
import time
from uuid import UUID

_MAX_RANDOM_B = (1 << 62) - 1


def uuid7(now_ns: int | None = None) -> UUID:
    """Return a UUIDv7.

    Layout: 48-bit Unix milliseconds, 4-bit version, 12 bits of randomness,
    2-bit RFC variant, 62 bits of randomness. Lexicographic order follows
    creation time for IDs created in the same process and clock.
    """

    milliseconds = (time.time_ns() if now_ns is None else now_ns) // 1_000_000
    value = (milliseconds & ((1 << 48) - 1)) << 80
    value |= 0b0111 << 76
    value |= secrets.randbits(12) << 64
    value |= 0b10 << 62
    value |= secrets.randbits(62) & _MAX_RANDOM_B
    return UUID(int=value)


def uuid7_from_timestamp(milliseconds: int) -> UUID:
    """Return a UUIDv7 with a fixed timestamp (test/backfill helper)."""

    return uuid7(milliseconds * 1_000_000)
