"""The collection loop against the real snapshot history (task 014a2b2).

The unit tests pin the rules with an in-memory store; these tests prove that the
same run is persisted, cached and throttled through PostgreSQL, including the
manual-entry dedupe that keeps a repeated run from double-counting one price.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import func, select

from travelos.market.adapters import CompetitorFetch, CompetitorPriceAdapter
from travelos.market.collection import (
    CollectionOutcome,
    CollectionPolicy,
    StaticSiteGate,
    collect_competitor_prices,
)
from travelos.market.models import ComparabilityBasis, CompetitorPrice, FetchStatus
from travelos.market.records import CompetitorSnapshot
from travelos.market.store import CompetitorSnapshotStore

pytestmark = pytest.mark.integration

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


class StubAdapter(CompetitorPriceAdapter):
    def __init__(
        self,
        site: str,
        *,
        cacheable: bool = True,
        status: FetchStatus = FetchStatus.OK,
        observations: tuple[CompetitorPrice, ...] = (),
        detail: str = "",
    ) -> None:
        self._site = site
        self._cacheable = cacheable
        self._status = status
        self._observations = list(observations)
        self._detail = detail
        self.calls = 0

    @property
    def site(self) -> str:
        return self._site

    @property
    def cacheable(self) -> bool:
        return self._cacheable

    def add(self, observation: CompetitorPrice) -> None:
        self._observations.append(observation)

    def fetch(self, basis: ComparabilityBasis, *, now: datetime) -> CompetitorFetch:
        del basis
        self.calls += 1
        if self._status is FetchStatus.OK:
            return CompetitorFetch(
                site=self._site,
                status=FetchStatus.OK,
                fetched_at=now,
                observations=tuple(self._observations),
                detail=self._detail,
            )
        return CompetitorFetch(
            site=self._site,
            status=self._status,
            fetched_at=now,
            detail=self._detail,
        )


def row_count(db) -> int:
    return db.execute(select(func.count()).select_from(CompetitorSnapshot)).scalar_one()


def test_a_fetched_run_is_stored_and_the_next_run_uses_the_cache(db) -> None:
    store = CompetitorSnapshotStore(db)
    adapter = StubAdapter("SiteA", observations=(price(),))
    gate = StaticSiteGate(enabled_sites=frozenset({"SiteA"}))

    first = collect_competitor_prices([adapter], basis(), store=store, gate=gate, now=NOW)

    assert first.sites[0].outcome is CollectionOutcome.FETCHED
    assert first.sites[0].stored == 1
    assert row_count(db) == 1
    assert [item.price_paise for item in first.observations] == [4_500_000]

    second = collect_competitor_prices(
        [adapter], basis(), store=store, gate=gate, now=NOW + timedelta(minutes=1)
    )

    assert second.sites[0].outcome is CollectionOutcome.CACHED
    assert adapter.calls == 1
    assert row_count(db) == 1


def test_a_failed_run_is_throttled_and_retried_after_the_interval(db) -> None:
    store = CompetitorSnapshotStore(db)
    adapter = StubAdapter("SiteA", status=FetchStatus.UNAVAILABLE, detail="adapter failed")
    gate = StaticSiteGate(enabled_sites=frozenset({"SiteA"}))
    policy = CollectionPolicy(min_interval=timedelta(minutes=10))

    first = collect_competitor_prices(
        [adapter], basis(), store=store, gate=gate, now=NOW, policy=policy
    )
    assert first.sites[0].outcome is CollectionOutcome.FETCHED
    assert row_count(db) == 1

    throttled = collect_competitor_prices(
        [adapter],
        basis(),
        store=store,
        gate=gate,
        now=NOW + timedelta(minutes=1),
        policy=policy,
    )
    assert throttled.sites[0].outcome is CollectionOutcome.THROTTLED
    assert adapter.calls == 1

    retried = collect_competitor_prices(
        [adapter],
        basis(),
        store=store,
        gate=gate,
        now=NOW + timedelta(minutes=11),
        policy=policy,
    )
    assert retried.sites[0].outcome is CollectionOutcome.FETCHED
    assert adapter.calls == 2
    assert row_count(db) == 2


def test_a_blocked_site_is_not_retried_until_an_operator_intervenes(db) -> None:
    store = CompetitorSnapshotStore(db)
    adapter = StubAdapter("SiteA", status=FetchStatus.BLOCKED, detail="challenge presented")
    gate = StaticSiteGate(enabled_sites=frozenset({"SiteA"}))

    collect_competitor_prices([adapter], basis(), store=store, gate=gate, now=NOW)
    later = collect_competitor_prices(
        [adapter], basis(), store=store, gate=gate, now=NOW + timedelta(days=3)
    )

    assert later.sites[0].outcome is CollectionOutcome.BLOCKED
    assert adapter.calls == 1
    assert row_count(db) == 1


def test_a_manual_source_is_never_hidden_and_never_stored_twice(db) -> None:
    store = CompetitorSnapshotStore(db)
    first_entry = price(site="MakeMyTrip", source_ref="manual/1")
    adapter = StubAdapter("MakeMyTrip", cacheable=False, observations=(first_entry,))
    gate = StaticSiteGate(enabled_sites=frozenset({"MakeMyTrip"}))

    first = collect_competitor_prices([adapter], basis(), store=store, gate=gate, now=NOW)
    assert first.sites[0].stored == 1

    second_entry = price(
        site="MakeMyTrip",
        source_ref="manual/2",
        price_paise=4_800_000,
        fetched_at=NOW - timedelta(minutes=1),
    )
    adapter.add(second_entry)
    second = collect_competitor_prices(
        [adapter], basis(), store=store, gate=gate, now=NOW + timedelta(minutes=1)
    )

    assert second.sites[0].outcome is CollectionOutcome.FETCHED
    assert adapter.calls == 2
    assert second.sites[0].stored == 1
    assert row_count(db) == 2
    assert [item.source_ref for item in second.observations] == ["manual/1", "manual/2"]

    third = collect_competitor_prices(
        [adapter], basis(), store=store, gate=gate, now=NOW + timedelta(minutes=2)
    )
    assert third.sites[0].stored == 0
    assert row_count(db) == 2


def test_a_disabled_site_contributes_no_observation(db) -> None:
    store = CompetitorSnapshotStore(db)
    adapter = StubAdapter("SiteA", observations=(price(),))

    result = collect_competitor_prices(
        [adapter], basis(), store=store, gate=StaticSiteGate(), now=NOW
    )

    assert result.sites[0].outcome is CollectionOutcome.DISABLED
    assert result.observations == ()
    assert adapter.calls == 0
    assert row_count(db) == 0
