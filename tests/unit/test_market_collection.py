"""Per-site collection policy: gates, cache, throttle and failure isolation (014a2b2).

The loop is tested with an in-memory store and fake adapters, so the rules are
pinned without a database: a disabled site is never contacted, a fresh snapshot
is reused instead of fetching, a recent failure is not retried, a blocked site
waits for a human, and manual entries are never hidden by the cache.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any
from uuid import UUID

import pytest

from travelos.core.config import KillSwitches, Settings
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.features import FeatureFlagService
from travelos.market.adapters import CompetitorFetch, CompetitorPriceAdapter
from travelos.market.collection import (
    CollectionOutcome,
    CollectionPolicy,
    FeatureFlagSiteGate,
    StaticSiteGate,
    collect_competitor_prices,
    site_flag_key,
)
from travelos.market.errors import MarketValidationError
from travelos.market.models import (
    ComparabilityBasis,
    CompetitorPrice,
    FetchStatus,
)
from travelos.market.store import SnapshotAttempt

NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
START = date(2027, 2, 10)
END = date(2027, 2, 13)


def basis() -> ComparabilityBasis:
    return ComparabilityBasis(
        product_type="flight",
        route="Delhi-Goa",
        start_date=START,
        end_date=END,
        adults=2,
        fare_class="economy",
        checked_bags=1,
        refundable=True,
        currency="INR",
    )


def price(site: str = "SiteA", **overrides: Any) -> CompetitorPrice:
    values: dict[str, Any] = {
        "site": site,
        "product_type": "flight",
        "route": "Delhi-Goa",
        "start_date": START,
        "end_date": END,
        "adults": 2,
        "fare_class": "economy",
        "checked_bags": 1,
        "refundable": True,
        "price_paise": 4_500_000,
        "currency": "INR",
        "fetched_at": NOW - timedelta(minutes=5),
        "source_ref": f"manual/{site}/1",
    }
    values.update(overrides)
    return CompetitorPrice.model_validate(values)


class FakeAdapter(CompetitorPriceAdapter):
    def __init__(
        self,
        site: str,
        *,
        cacheable: bool = True,
        observations: tuple[CompetitorPrice, ...] = (),
        status: FetchStatus = FetchStatus.OK,
        detail: str = "",
        error: Exception | None = None,
    ) -> None:
        self._site = site
        self._cacheable = cacheable
        self._observations = observations
        self._status = status
        self._detail = detail
        self._error = error
        self.calls = 0

    @property
    def site(self) -> str:
        return self._site

    @property
    def cacheable(self) -> bool:
        return self._cacheable

    def fetch(self, basis: ComparabilityBasis, *, now: datetime) -> CompetitorFetch:
        del basis
        self.calls += 1
        if self._error is not None:
            raise self._error
        if self._status is FetchStatus.OK:
            return CompetitorFetch(
                site=self._site,
                status=FetchStatus.OK,
                fetched_at=now,
                observations=self._observations,
                detail=self._detail,
            )
        return CompetitorFetch(
            site=self._site,
            status=self._status,
            fetched_at=now,
            detail=self._detail,
        )


class FakeStore:
    def __init__(self) -> None:
        self.observations: list[CompetitorPrice] = []
        self.attempts: dict[str, SnapshotAttempt] = {}
        self.recorded: list[CompetitorFetch] = []

    def latest(
        self,
        basis: ComparabilityBasis,
        *,
        now: datetime,
        max_age: timedelta = timedelta(hours=24),
        site: str | None = None,
    ) -> tuple[CompetitorPrice, ...] | None:
        del basis
        rows = [
            item
            for item in self.observations
            if (site is None or item.site == site) and item.fetched_at >= now - max_age
        ]
        rows.sort(key=lambda item: (item.site, item.price_paise, item.fetched_at))
        return tuple(rows) or None

    def last_attempt(self, basis: ComparabilityBasis, *, site: str) -> SnapshotAttempt | None:
        del basis
        return self.attempts.get(site)

    def stored_observations(
        self, basis: ComparabilityBasis, *, site: str
    ) -> tuple[CompetitorPrice, ...]:
        del basis
        return tuple(item for item in self.observations if item.site == site)

    def record(
        self,
        basis: ComparabilityBasis,
        fetch: CompetitorFetch,
        *,
        assessments: object = None,
        actor: object = None,
    ) -> tuple[object, ...]:
        del basis, assessments, actor
        self.recorded.append(fetch)
        self.attempts[fetch.site] = SnapshotAttempt(fetch.status, fetch.fetched_at)
        if fetch.status is FetchStatus.OK:
            self.observations.extend(fetch.observations)
            return tuple(fetch.observations)
        return (object(),)


class FakeSource:
    def __init__(self) -> None:
        self.values: dict[tuple[UUID, str, str], bool | None] = {}

    def set(self, key: str, value: bool, *, tenant_id: UUID = DEFAULT_TENANT_ID) -> None:
        self.values[(tenant_id, key, "global")] = value

    def lookup(self, *, tenant_id: UUID, key: str, scope: str) -> bool | None:
        return self.values.get((tenant_id, key, scope))


def make_flags(*, switches: KillSwitches | None = None) -> tuple[FeatureFlagService, FakeSource]:
    source = FakeSource()
    settings = Settings(_env_file=None, kill_switches=switches or KillSwitches())
    return FeatureFlagService(source, settings), source


# ---------------------------------------------------------------------------
# Site gates
# ---------------------------------------------------------------------------


def test_a_static_gate_contacts_no_site_until_it_is_listed() -> None:
    gate = StaticSiteGate()

    assert gate.is_enabled("SiteA") is False
    assert StaticSiteGate(enabled_sites=frozenset({"SiteA"})).is_enabled("SiteA") is True


def test_the_site_flag_key_follows_the_existing_kill_switch_names() -> None:
    assert site_flag_key("MakeMyTrip") == "scraper_makemytrip"
    assert site_flag_key("Goibibo") == "scraper_goibibo"
    assert site_flag_key("Booking.com") == "scraper_booking_com"
    assert site_flag_key("Agoda") == "scraper_agoda"
    assert site_flag_key("EaseMyTrip") == "scraper_easemytrip"
    assert site_flag_key("Yatra") == "scraper_yatra"


@pytest.mark.parametrize("site", ["", "   ", "...", "!!"])
def test_a_site_without_a_usable_name_has_no_flag(site: str) -> None:
    with pytest.raises(MarketValidationError, match="site"):
        site_flag_key(site)


def test_the_feature_gate_needs_both_the_operator_switch_and_the_owner_flag() -> None:
    flags, source = make_flags()
    gate = FeatureFlagSiteGate(flags)

    assert gate.is_enabled("MakeMyTrip") is False

    source.set("scraper_makemytrip", True)
    assert gate.is_enabled("MakeMyTrip") is False

    flags.invalidate()
    flags_operator_on, _ = make_flags(switches=KillSwitches(scraper_makemytrip=True))
    assert FeatureFlagSiteGate(flags_operator_on).is_enabled("MakeMyTrip") is False


def test_the_owner_flag_enables_a_site_the_operator_permits() -> None:
    flags, source = make_flags(switches=KillSwitches(scraper_makemytrip=True))
    source.set("scraper_makemytrip", True)
    gate = FeatureFlagSiteGate(flags)

    assert gate.is_enabled("MakeMyTrip") is True
    assert gate.is_enabled("Goibibo") is False


def test_an_unknown_site_cannot_be_enabled_by_a_database_row() -> None:
    flags, source = make_flags()
    source.set("scraper_sitea", True)
    gate = FeatureFlagSiteGate(flags)

    assert gate.is_enabled("SiteA") is False


# ---------------------------------------------------------------------------
# Collection policy
# ---------------------------------------------------------------------------


def test_policy_durations_must_be_positive() -> None:
    with pytest.raises(ValueError, match="cache_max_age"):
        CollectionPolicy(cache_max_age=timedelta(0))
    with pytest.raises(ValueError, match="min_interval"):
        CollectionPolicy(min_interval=timedelta(seconds=-1))


def test_the_loop_needs_an_aware_clock() -> None:
    adapter = FakeAdapter("SiteA", observations=(price(),))

    with pytest.raises(ValueError, match="timezone-aware"):
        collect_competitor_prices(
            [adapter],
            basis(),
            store=FakeStore(),
            gate=StaticSiteGate(enabled_sites=frozenset({"SiteA"})),
            now=datetime(2026, 11, 1, 10, 0),
        )


def test_a_disabled_site_is_never_contacted() -> None:
    adapter = FakeAdapter("SiteA", observations=(price(),))
    store = FakeStore()

    result = collect_competitor_prices(
        [adapter], basis(), store=store, gate=StaticSiteGate(), now=NOW
    )

    assert adapter.calls == 0
    assert store.recorded == []
    assert [site.outcome for site in result.sites] == [CollectionOutcome.DISABLED]
    assert result.observations == ()


def test_an_enabled_site_is_fetched_recorded_and_returned() -> None:
    adapter = FakeAdapter("SiteA", observations=(price(),))
    store = FakeStore()

    result = collect_competitor_prices(
        [adapter],
        basis(),
        store=store,
        gate=StaticSiteGate(enabled_sites=frozenset({"SiteA"})),
        now=NOW,
    )

    assert adapter.calls == 1
    assert len(store.recorded) == 1
    assert result.sites[0].outcome is CollectionOutcome.FETCHED
    assert result.sites[0].stored == 1
    assert [item.price_paise for item in result.observations] == [4_500_000]


def test_a_fresh_snapshot_is_reused_instead_of_fetching() -> None:
    stored = price(fetched_at=NOW - timedelta(minutes=30))
    adapter = FakeAdapter("SiteA", observations=(price(price_paise=9_000_000),))
    store = FakeStore()
    store.observations.append(stored)
    store.attempts["SiteA"] = SnapshotAttempt(FetchStatus.OK, stored.fetched_at)

    result = collect_competitor_prices(
        [adapter],
        basis(),
        store=store,
        gate=StaticSiteGate(enabled_sites=frozenset({"SiteA"})),
        now=NOW,
    )

    assert adapter.calls == 0
    assert store.recorded == []
    assert result.sites[0].outcome is CollectionOutcome.CACHED
    assert result.observations == (stored,)


def test_a_recent_failed_run_is_throttled() -> None:
    adapter = FakeAdapter("SiteA", observations=(price(),))
    store = FakeStore()
    store.attempts["SiteA"] = SnapshotAttempt(FetchStatus.UNAVAILABLE, NOW - timedelta(minutes=1))

    result = collect_competitor_prices(
        [adapter],
        basis(),
        store=store,
        gate=StaticSiteGate(enabled_sites=frozenset({"SiteA"})),
        now=NOW,
    )

    assert adapter.calls == 0
    assert result.sites[0].outcome is CollectionOutcome.THROTTLED


def test_a_failure_older_than_the_minimum_interval_is_retried() -> None:
    adapter = FakeAdapter("SiteA", observations=(price(),))
    store = FakeStore()
    store.attempts["SiteA"] = SnapshotAttempt(FetchStatus.UNAVAILABLE, NOW - timedelta(minutes=11))

    result = collect_competitor_prices(
        [adapter],
        basis(),
        store=store,
        gate=StaticSiteGate(enabled_sites=frozenset({"SiteA"})),
        now=NOW,
    )

    assert adapter.calls == 1
    assert result.sites[0].outcome is CollectionOutcome.FETCHED


def test_a_blocked_site_is_not_retried_even_after_the_interval() -> None:
    adapter = FakeAdapter("SiteA", observations=(price(),))
    store = FakeStore()
    store.attempts["SiteA"] = SnapshotAttempt(FetchStatus.BLOCKED, NOW - timedelta(days=2))

    result = collect_competitor_prices(
        [adapter],
        basis(),
        store=store,
        gate=StaticSiteGate(enabled_sites=frozenset({"SiteA"})),
        now=NOW,
    )

    assert adapter.calls == 0
    assert result.sites[0].outcome is CollectionOutcome.BLOCKED


def test_a_block_is_surfaced_even_when_older_prices_are_still_fresh() -> None:
    stored = price(fetched_at=NOW - timedelta(minutes=30))
    store = FakeStore()
    store.observations.append(stored)
    store.attempts["SiteA"] = SnapshotAttempt(FetchStatus.BLOCKED, NOW - timedelta(minutes=10))
    adapter = FakeAdapter("SiteA", observations=(price(price_paise=1),))

    result = collect_competitor_prices(
        [adapter],
        basis(),
        store=store,
        gate=StaticSiteGate(enabled_sites=frozenset({"SiteA"})),
        now=NOW,
    )

    assert adapter.calls == 0
    assert result.sites[0].outcome is CollectionOutcome.BLOCKED
    assert result.observations == (stored,)


def test_manual_entries_bypass_the_cache_and_only_new_ones_are_stored() -> None:
    already_stored = price(site="MakeMyTrip", source_ref="manual/1")
    new_entry = price(
        site="MakeMyTrip",
        source_ref="manual/2",
        price_paise=4_800_000,
        fetched_at=NOW - timedelta(minutes=1),
    )
    adapter = FakeAdapter(
        "MakeMyTrip",
        cacheable=False,
        observations=(already_stored, new_entry),
    )
    store = FakeStore()
    store.observations.append(already_stored)
    store.attempts["MakeMyTrip"] = SnapshotAttempt(
        FetchStatus.UNAVAILABLE, NOW - timedelta(minutes=1)
    )

    result = collect_competitor_prices(
        [adapter],
        basis(),
        store=store,
        gate=StaticSiteGate(enabled_sites=frozenset({"MakeMyTrip"})),
        now=NOW,
    )

    assert adapter.calls == 1
    assert len(store.recorded) == 1
    assert store.recorded[0].observations == (new_entry,)
    assert result.sites[0].outcome is CollectionOutcome.FETCHED
    assert result.sites[0].stored == 1


def test_manual_entries_that_were_all_stored_write_nothing() -> None:
    entry = price(site="MakeMyTrip", source_ref="manual/1")
    adapter = FakeAdapter("MakeMyTrip", cacheable=False, observations=(entry,))
    store = FakeStore()
    store.observations.append(entry)

    result = collect_competitor_prices(
        [adapter],
        basis(),
        store=store,
        gate=StaticSiteGate(enabled_sites=frozenset({"MakeMyTrip"})),
        now=NOW,
    )

    assert store.recorded == []
    assert result.sites[0].outcome is CollectionOutcome.FETCHED
    assert result.sites[0].stored == 0


def test_an_empty_manual_source_is_not_recorded_as_a_failed_run() -> None:
    adapter = FakeAdapter(
        "MakeMyTrip",
        cacheable=False,
        status=FetchStatus.UNAVAILABLE,
        detail="no manual entries recorded",
    )
    store = FakeStore()

    collect_competitor_prices(
        [adapter],
        basis(),
        store=store,
        gate=StaticSiteGate(enabled_sites=frozenset({"MakeMyTrip"})),
        now=NOW,
    )

    assert adapter.calls == 1
    assert store.recorded == []


def test_observations_cover_cached_and_fetched_sites_in_a_stable_order() -> None:
    cached = price(site="SiteA", price_paise=5_000_000, fetched_at=NOW - timedelta(minutes=30))
    fetched = price(site="SiteB", price_paise=4_000_000, fetched_at=NOW)
    store = FakeStore()
    store.observations.append(cached)
    adapters = [
        FakeAdapter("SiteA", observations=(price(site="SiteA", price_paise=1),)),
        FakeAdapter("SiteB", observations=(fetched,)),
    ]

    result = collect_competitor_prices(
        adapters,
        basis(),
        store=store,
        gate=StaticSiteGate(enabled_sites=frozenset({"SiteA", "SiteB"})),
        now=NOW,
    )

    assert [(item.site, item.price_paise) for item in result.observations] == [
        ("SiteA", 5_000_000),
        ("SiteB", 4_000_000),
    ]


def test_a_disabled_site_does_not_influence_the_observations() -> None:
    stored = price(site="SiteB")
    store = FakeStore()
    store.observations.append(stored)
    adapter = FakeAdapter("SiteB", observations=(stored,))

    result = collect_competitor_prices(
        [adapter],
        basis(),
        store=store,
        gate=StaticSiteGate(enabled_sites=frozenset({"SiteA"})),
        now=NOW,
    )

    assert result.observations == ()


def test_one_raising_adapter_does_not_stop_another_site() -> None:
    broken = FakeAdapter("SiteA", error=TimeoutError("https://portal/?token=secret"))
    healthy = FakeAdapter("SiteB", observations=(price(site="SiteB"),))
    store = FakeStore()

    result = collect_competitor_prices(
        [broken, healthy],
        basis(),
        store=store,
        gate=StaticSiteGate(enabled_sites=frozenset({"SiteA", "SiteB"})),
        now=NOW,
    )

    assert result.sites[0].fetch is not None
    assert result.sites[0].fetch.status is FetchStatus.UNAVAILABLE
    assert result.sites[0].fetch.detail == "adapter failed (TimeoutError)"
    assert "secret" not in result.sites[0].fetch.detail
    assert result.sites[1].stored == 1
    assert [item.site for item in result.observations] == ["SiteB"]
