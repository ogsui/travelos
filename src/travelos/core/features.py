"""Feature flags and kill switches.

Two layers, by design:

* Environment settings (``KILL_SWITCHES__*``) are the operator's hard switch.
  When an operator turns a capability off there, no database flag can turn it
  back on.
* ``feature_flags`` rows are the owner's audited switch. They default to off
  when no row exists.

Effective value = operator switch AND owner switch, where an absent database
row means OFF. Every channel, scraper, ad and social capability therefore
stays off until both a deployment decision and an audited owner decision exist.

The read cache is per process with a short TTL; writes invalidate it locally.
Other workers converge within the TTL (or immediately on their next write).
"""

from __future__ import annotations

import dataclasses
import logging
import time
from collections.abc import Callable
from typing import Any, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from travelos.core.config import KillSwitches, Settings
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.models import FeatureFlag

logger = logging.getLogger("travelos.features")

DEFAULT_SCOPE = "global"
DEFAULT_TTL_SECONDS = 30.0


class FlagSource(Protocol):
    """Reads the stored flag value, or ``None`` when no row exists."""

    def lookup(self, *, tenant_id: UUID, key: str, scope: str) -> bool | None:
        """Return the stored boolean, ``None`` if absent, ``False`` if invalid."""
        ...


class DatabaseFlagSource:
    """Reads ``feature_flags`` rows. Malformed rows fail closed."""

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def lookup(self, *, tenant_id: UUID, key: str, scope: str) -> bool | None:
        with self._session_factory() as db:
            value: Any = db.execute(
                select(FeatureFlag.value).where(
                    FeatureFlag.tenant_id == tenant_id,
                    FeatureFlag.key == key,
                    FeatureFlag.scope == scope,
                )
            ).scalar_one_or_none()
        if value is None:
            return None
        enabled = value.get("enabled") if isinstance(value, dict) else None
        if isinstance(enabled, bool):
            return enabled
        logger.warning("feature flag %r has a malformed value; treating as off", key)
        return False


@dataclasses.dataclass(frozen=True, slots=True)
class FlagState:
    key: str
    enabled: bool
    source: str  # operator_kill_switch | database | default


class FeatureFlagService:
    def __init__(
        self,
        source: FlagSource,
        settings: Settings,
        *,
        ttl_seconds: float = DEFAULT_TTL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        if ttl_seconds <= 0:
            raise ValueError("ttl_seconds must be positive")
        self._source = source
        self._settings = settings
        self._ttl = ttl_seconds
        self._clock = clock
        self._cache: dict[tuple[UUID, str, str], tuple[bool | None, float]] = {}

    def _operator_value(self, key: str) -> bool | None:
        if key not in KillSwitches.model_fields:
            return None
        return bool(getattr(self._settings.kill_switches, key))

    def _stored_value(self, tenant_id: UUID, key: str, scope: str) -> bool | None:
        cache_key = (tenant_id, key, scope)
        now = self._clock()
        cached = self._cache.get(cache_key)
        if cached is not None and cached[1] > now:
            return cached[0]
        value = self._source.lookup(tenant_id=tenant_id, key=key, scope=scope)
        self._cache[cache_key] = (value, now + self._ttl)
        return value

    def state(
        self, key: str, *, tenant_id: UUID = DEFAULT_TENANT_ID, scope: str = DEFAULT_SCOPE
    ) -> FlagState:
        operator = self._operator_value(key)
        if operator is False:
            return FlagState(key=key, enabled=False, source="operator_kill_switch")

        stored = self._stored_value(tenant_id, key, scope)
        if stored is not None:
            return FlagState(key=key, enabled=stored, source="database")
        # No owner decision yet: the capability stays off even when the
        # deployment permits it. Enabling is always an explicit, audited act.
        return FlagState(key=key, enabled=False, source="default")

    def is_enabled(
        self, key: str, *, tenant_id: UUID = DEFAULT_TENANT_ID, scope: str = DEFAULT_SCOPE
    ) -> bool:
        return self.state(key, tenant_id=tenant_id, scope=scope).enabled

    def invalidate(self, key: str | None = None, *, scope: str | None = None) -> None:
        """Drop cached values. Called after every write."""

        if key is None and scope is None:
            self._cache.clear()
            return
        for cache_key in list(self._cache):
            _, cached_key, cached_scope = cache_key
            if (key is None or cached_key == key) and (scope is None or cached_scope == scope):
                del self._cache[cache_key]
