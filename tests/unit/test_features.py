"""Feature-flag and kill-switch tests with a fake source and a fake clock."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from travelos.core.config import KillSwitches, Settings
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.features import DEFAULT_SCOPE, FeatureFlagService, FlagState


class FakeSource:
    def __init__(self) -> None:
        self.values: dict[tuple[UUID, str, str], bool | None] = {}
        self.calls: list[tuple[UUID, str, str]] = []

    def set(self, key: str, value: bool, *, tenant_id: UUID = DEFAULT_TENANT_ID) -> None:
        self.values[(tenant_id, key, DEFAULT_SCOPE)] = value

    def lookup(self, *, tenant_id: UUID, key: str, scope: str) -> bool | None:
        self.calls.append((tenant_id, key, scope))
        return self.values.get((tenant_id, key, scope))


class FakeClock:
    def __init__(self, now: float = 1_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_service(
    *,
    switches: KillSwitches | None = None,
    ttl: float = 30.0,
) -> tuple[FeatureFlagService, FakeSource, FakeClock]:
    source = FakeSource()
    clock = FakeClock()
    settings = Settings(
        _env_file=None,
        kill_switches=switches or KillSwitches(),
    )
    service = FeatureFlagService(source, settings, ttl_seconds=ttl, clock=clock)
    return service, source, clock


def test_known_switch_is_off_when_operator_and_database_are_off() -> None:
    service, source, _ = make_service()

    state = service.state("email")

    assert state == FlagState(key="email", enabled=False, source="operator_kill_switch")
    assert source.calls == []


def test_operator_on_without_a_row_is_still_off() -> None:
    service, source, _ = make_service(switches=KillSwitches(email=True))

    state = service.state("email")

    assert state == FlagState(key="email", enabled=False, source="default")
    assert source.calls == [(DEFAULT_TENANT_ID, "email", DEFAULT_SCOPE)]


def test_database_value_enables_a_capability_the_operator_permits() -> None:
    service, source, _ = make_service(switches=KillSwitches(email=True))
    source.set("email", True)

    assert service.state("email") == FlagState(key="email", enabled=True, source="database")


def test_database_value_can_disable_a_capability_the_operator_permits() -> None:
    service, source, _ = make_service(switches=KillSwitches(email=True))
    source.set("email", False)

    assert service.state("email") == FlagState(key="email", enabled=False, source="database")


def test_database_value_cannot_override_the_operator_kill_switch() -> None:
    service, source, _ = make_service()
    source.set("email", True)

    state = service.state("email")

    assert state.enabled is False
    assert state.source == "operator_kill_switch"
    # The stored value is not even read when the operator forbids the capability.
    assert source.calls == []


def test_unknown_flag_is_off_by_default() -> None:
    service, _, _ = make_service()

    assert service.state("not_a_switch") == FlagState(
        key="not_a_switch", enabled=False, source="default"
    )


def test_is_enabled_delegates_to_state() -> None:
    service, source, _ = make_service(switches=KillSwitches(email=True))
    source.set("email", True)

    assert service.is_enabled("email") is True
    assert service.is_enabled("sms") is False


def test_lookup_is_cached_within_the_ttl() -> None:
    service, source, clock = make_service(switches=KillSwitches(email=True), ttl=30)
    source.set("email", True)

    assert service.is_enabled("email") is True
    source.set("email", False)
    clock.advance(20)
    assert service.is_enabled("email") is True

    assert len(source.calls) == 1


def test_cache_expires_after_the_ttl() -> None:
    service, source, clock = make_service(switches=KillSwitches(email=True), ttl=30)
    source.set("email", True)
    assert service.is_enabled("email") is True

    source.set("email", False)
    clock.advance(31)

    assert service.is_enabled("email") is False
    assert len(source.calls) == 2


def test_invalidate_by_key_refetches_only_that_key() -> None:
    service, source, _ = make_service(
        switches=KillSwitches(email=True, sms=True),
    )
    source.set("email", True)
    source.set("sms", True)
    service.is_enabled("email")
    service.is_enabled("sms")

    source.set("email", False)
    source.set("sms", False)
    service.invalidate("email")

    assert service.is_enabled("email") is False
    assert service.is_enabled("sms") is True
    assert len(source.calls) == 3


def test_invalidate_all_clears_every_cached_value() -> None:
    service, source, _ = make_service(switches=KillSwitches(email=True, sms=True))
    source.set("email", True)
    source.set("sms", True)
    service.is_enabled("email")
    service.is_enabled("sms")

    source.set("email", False)
    source.set("sms", False)
    service.invalidate()

    assert service.is_enabled("email") is False
    assert service.is_enabled("sms") is False
    assert len(source.calls) == 4


def test_cache_is_scoped_by_tenant_and_scope() -> None:
    service, source, _ = make_service(switches=KillSwitches(email=True))
    other_tenant = uuid4()
    source.set("email", True)
    source.values[(other_tenant, "email", DEFAULT_SCOPE)] = False
    source.values[(DEFAULT_TENANT_ID, "email", "tenant-x")] = False

    assert service.is_enabled("email") is True
    assert service.is_enabled("email", tenant_id=other_tenant) is False
    assert service.is_enabled("email", scope="tenant-x") is False
    assert len(source.calls) == 3


def test_negative_ttl_is_not_allowed() -> None:
    with pytest.raises(ValueError, match="positive"):
        FeatureFlagService(FakeSource(), Settings(_env_file=None), ttl_seconds=-1)
