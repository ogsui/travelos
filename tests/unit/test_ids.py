"""UUIDv7 tests: version, variant and time sortability."""

from __future__ import annotations

from uuid import UUID

from travelos.core.ids import uuid7, uuid7_from_timestamp


def test_uuid7_has_rfc_version_and_variant() -> None:
    value = uuid7()

    assert isinstance(value, UUID)
    assert value.version == 7
    assert value.variant == "specified in RFC 4122"


def test_uuid7_is_unique_per_call() -> None:
    assert len({uuid7() for _ in range(1_000)}) == 1_000


def test_uuid7_sorts_by_creation_time() -> None:
    first = uuid7(now_ns=1_700_000_000_000_000_000)
    later = uuid7(now_ns=1_700_000_000_001_000_000)

    assert first.int < later.int
    assert uuid7_from_timestamp(1_700_000_000_001).int > first.int


def test_uuid7_masks_to_48_bit_timestamp() -> None:
    value = uuid7_from_timestamp(0x0000_0000_0001)

    assert value.int >> 80 == 1
