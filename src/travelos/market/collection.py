"""Collect competitor prices under an explicit per-site policy (task 014a2b2).

The loop composes the pieces that already exist: an adapter answers one question
for one site, the store keeps an append-only history, and the owner's per-site
flag decides whether that site may be contacted at all. Nothing here touches a
portal, a browser or an LLM, and no price is calculated.

A site starts disabled. ``StaticSiteGate`` is the deterministic default (nothing
enabled) and ``FeatureFlagSiteGate`` maps a site name onto the existing
``scraper_*`` kill switches: the operator's environment switch and the owner's
audited database flag must both be on. Reusing those keys means a new site is a
deliberate deployment change, and the admin flag endpoint keeps its explicit
allowlist.

Collection rules:

* A fresh stored observation satisfies a cacheable source, so a site is not
  fetched twice inside ``cache_max_age``.
* A recent ``unavailable`` run throttles the site for ``min_interval``; a
  ``blocked`` run is never retried automatically because an anti-bot/CAPTCHA
  block needs an operator, not a retry (AGENTS.md).
* Manual entry is not cacheable: a human can add a price at any time, so the
  cache must never hide a new entry. Entries already stored are not written
  again, and an empty manual source is a state, not a failed attempt.
* Observations returned to the caller cover every enabled adapter's fresh rows,
  cached or just fetched, in a deterministic order.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol
from uuid import UUID

from travelos.core.audit import Actor
from travelos.core.config import KillSwitches
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.features import DEFAULT_SCOPE, FeatureFlagService
from travelos.market.adapters import (
    CompetitorFetch,
    CompetitorPriceAdapter,
    fetch_competitor_prices,
)
from travelos.market.errors import MarketValidationError
from travelos.market.models import ComparabilityBasis, CompetitorPrice, FetchStatus
from travelos.market.store import SnapshotAttempt

_SITE_SLUG = re.compile(r"[^a-z0-9]+")


class SiteGate(Protocol):
    """Decides whether a site may be contacted at all."""

    def is_enabled(self, site: str) -> bool:
        """Return ``True`` only when the owner has enabled this site."""
        ...


@dataclass(frozen=True, slots=True)
class StaticSiteGate:
    """A fixed allowlist. The empty default contacts no site."""

    enabled_sites: frozenset[str] = frozenset()

    def is_enabled(self, site: str) -> bool:
        return site.strip() in self.enabled_sites


def site_flag_key(site: str) -> str:
    """Map a site name onto its existing ``scraper_*`` kill switch.

    ``MakeMyTrip`` becomes ``scraper_makemytrip`` and ``Booking.com`` becomes
    ``scraper_booking_com``, matching the names already declared in
    ``KillSwitches``. A name that cannot produce a key is refused rather than
    silently mapped to something that could collide with another site.
    """

    slug = _SITE_SLUG.sub("_", site.strip().casefold()).strip("_")
    if not slug:
        raise MarketValidationError("site must have a usable name to map to a flag")
    return f"scraper_{slug}"


class FeatureFlagSiteGate:
    """Requires the operator's per-site switch and the owner's database flag."""

    def __init__(
        self,
        flags: FeatureFlagService,
        *,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        scope: str = DEFAULT_SCOPE,
    ) -> None:
        self._flags = flags
        self._tenant_id = tenant_id
        self._scope = scope

    def is_enabled(self, site: str) -> bool:
        key = site_flag_key(site)
        if key not in KillSwitches.switch_names():
            # No deployment-level decision exists for this site, so a database
            # row must not be able to turn it on by itself.
            return False
        return self._flags.is_enabled(key, tenant_id=self._tenant_id, scope=self._scope)


@dataclass(frozen=True, slots=True)
class CollectionPolicy:
    """How long a stored price is reused and how often a failed site is retried.

    These are engineering safety defaults, not owner policy. They are listed in
    ``docs/BACKLOG.md`` for confirmation.
    """

    cache_max_age: timedelta = timedelta(hours=24)
    min_interval: timedelta = timedelta(minutes=10)

    def __post_init__(self) -> None:
        for name in ("cache_max_age", "min_interval"):
            value = getattr(self, name)
            if not isinstance(value, timedelta) or value <= timedelta(0):
                raise ValueError(f"{name} must be a positive timedelta")


class CollectionOutcome(StrEnum):
    """What the loop did for one site."""

    DISABLED = "disabled"
    BLOCKED = "blocked"
    THROTTLED = "throttled"
    CACHED = "cached"
    FETCHED = "fetched"


@dataclass(frozen=True, slots=True)
class SiteCollection:
    """One site's outcome, including the adapter result when it ran."""

    site: str
    outcome: CollectionOutcome
    fetch: CompetitorFetch | None = None
    stored: int = 0


@dataclass(frozen=True, slots=True)
class CollectionResult:
    """One collection run for one product question."""

    basis: ComparabilityBasis
    sites: tuple[SiteCollection, ...]
    observations: tuple[CompetitorPrice, ...]


class SnapshotStore(Protocol):
    """The store behaviour the loop needs (``CompetitorSnapshotStore``)."""

    def latest(
        self,
        basis: ComparabilityBasis,
        *,
        now: datetime,
        max_age: timedelta = ...,
        site: str | None = None,
    ) -> tuple[CompetitorPrice, ...] | None: ...

    def last_attempt(self, basis: ComparabilityBasis, *, site: str) -> SnapshotAttempt | None: ...

    def stored_observations(
        self, basis: ComparabilityBasis, *, site: str
    ) -> tuple[CompetitorPrice, ...]: ...

    def record(
        self,
        basis: ComparabilityBasis,
        fetch: CompetitorFetch,
        *,
        actor: Actor | None = None,
    ) -> Sequence[object]: ...


def collect_competitor_prices(
    adapters: Sequence[CompetitorPriceAdapter],
    basis: ComparabilityBasis,
    *,
    store: SnapshotStore,
    gate: SiteGate,
    now: datetime,
    policy: CollectionPolicy | None = None,
    actor: Actor | None = None,
) -> CollectionResult:
    """Run every enabled adapter once, respecting the cache and throttle rules.

    The caller owns the transaction: the store flushes but does not commit, so a
    later failure can roll the whole run back.
    """

    moment = _aware_utc(now)
    rules = policy or CollectionPolicy()
    sites: list[SiteCollection] = []
    enabled: dict[str, bool] = {}
    for adapter in adapters:
        enabled.setdefault(adapter.site, gate.is_enabled(adapter.site))

    for adapter in adapters:
        site = adapter.site
        if not enabled[site]:
            sites.append(SiteCollection(site=site, outcome=CollectionOutcome.DISABLED))
            continue

        if adapter.cacheable:
            last = store.last_attempt(basis, site=site)
            if last is not None and last.status is FetchStatus.BLOCKED:
                # A block is surfaced even when older prices are still fresh:
                # the operator must know the site needs attention, and the loop
                # must not contact it again.
                sites.append(SiteCollection(site=site, outcome=CollectionOutcome.BLOCKED))
                continue

            cached = store.latest(basis, now=moment, max_age=rules.cache_max_age, site=site)
            if cached is not None:
                sites.append(SiteCollection(site=site, outcome=CollectionOutcome.CACHED))
                continue

            if last is not None and moment - last.fetched_at < rules.min_interval:
                sites.append(SiteCollection(site=site, outcome=CollectionOutcome.THROTTLED))
                continue

        # One adapter at a time, through the same failure-isolating helper the
        # batch API uses, so an exception never carries its message into a
        # snapshot or a staff screen.
        fetch = fetch_competitor_prices([adapter], basis, now=moment)[0]

        if not adapter.cacheable:
            if fetch.status is not FetchStatus.OK:
                # A manual source with nothing to enter is a state, not a failed
                # attempt; recording it every run would flood the history.
                sites.append(
                    SiteCollection(site=site, outcome=CollectionOutcome.FETCHED, fetch=fetch)
                )
                continue
            new = _new_observations(store, basis, fetch)
            if not new:
                # The cache must never hide a new entry, but an already stored
                # entry is not new data and must not be appended twice.
                sites.append(
                    SiteCollection(site=site, outcome=CollectionOutcome.FETCHED, fetch=fetch)
                )
                continue
            to_store = CompetitorFetch(
                site=fetch.site,
                status=FetchStatus.OK,
                fetched_at=fetch.fetched_at,
                observations=new,
                detail=fetch.detail,
            )
        else:
            to_store = fetch

        rows = store.record(basis, to_store, actor=actor)
        sites.append(
            SiteCollection(
                site=site,
                outcome=CollectionOutcome.FETCHED,
                fetch=fetch,
                stored=len(rows),
            )
        )

    return CollectionResult(
        basis=basis,
        sites=tuple(sites),
        observations=_observations(adapters, enabled, store, basis, moment, rules.cache_max_age),
    )


def _new_observations(
    store: SnapshotStore,
    basis: ComparabilityBasis,
    fetch: CompetitorFetch,
) -> tuple[CompetitorPrice, ...]:
    seen = {_identity(item) for item in store.stored_observations(basis, site=fetch.site)}
    fresh: list[CompetitorPrice] = []
    for item in fetch.observations:
        identity = _identity(item)
        if identity in seen:
            continue
        seen.add(identity)
        fresh.append(item)
    return tuple(fresh)


def _observations(
    adapters: Sequence[CompetitorPriceAdapter],
    enabled: dict[str, bool],
    store: SnapshotStore,
    basis: ComparabilityBasis,
    moment: datetime,
    max_age: timedelta,
) -> tuple[CompetitorPrice, ...]:
    by_identity: dict[tuple[object, ...], CompetitorPrice] = {}
    for adapter in adapters:
        if not enabled[adapter.site]:
            continue
        for item in store.latest(basis, now=moment, max_age=max_age, site=adapter.site) or ():
            by_identity[_sort_key(item)] = item
    return tuple(sorted(by_identity.values(), key=_sort_key))


def _identity(item: CompetitorPrice) -> tuple[object, ...]:
    return (item.fetched_at, item.price_paise, item.currency, item.source_ref)


def _sort_key(item: CompetitorPrice) -> tuple[object, ...]:
    return (item.site, item.price_paise, item.fetched_at, item.source_ref or "")


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)
