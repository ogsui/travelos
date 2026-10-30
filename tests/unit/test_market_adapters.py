"""Competitor price adapters: manual entry and licensed-data stub (task 014a2a).

No adapter here uses the network. Manual entry is the dashboard hook for prices a
human copied after the owner authorized the site; the licensed-data adapter is a
deliberate stub until an owner-supplied provider contract exists. A failing
adapter must never stop another site's prices from reaching the comparator.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import pytest

from travelos.market.adapters import (
    CompetitorFetch,
    LicensedDataAdapter,
    ManualEntryAdapter,
    competitor_observations,
    fetch_competitor_prices,
)
from travelos.market.comparability import compare_competitor_prices
from travelos.market.models import (
    ComparabilityBasis,
    ComparisonWarning,
    CompetitorPrice,
    FetchStatus,
    ObservationStatus,
)

NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
START = date(2027, 2, 10)
END = date(2027, 2, 13)


def basis(**overrides: Any) -> ComparabilityBasis:
    values: dict[str, Any] = {
        "product_type": "flight",
        "route": "Delhi-Goa",
        "start_date": START,
        "end_date": END,
        "adults": 2,
        "fare_class": "economy",
        "checked_bags": 1,
        "refundable": True,
        "currency": "INR",
    }
    values.update(overrides)
    return ComparabilityBasis.model_validate(values)


def price(**overrides: Any) -> CompetitorPrice:
    values: dict[str, Any] = {
        "site": "ManualSite",
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
        "status": ObservationStatus.OK,
        "source_ref": "manual",
    }
    values.update(overrides)
    return CompetitorPrice.model_validate(values)


class BrokenAdapter:
    """An adapter that raises; used to prove one failure cannot stop the others."""

    def __init__(self, site: str = "BrokenSite") -> None:
        self._site = site

    @property
    def site(self) -> str:
        return self._site

    def fetch(self, basis: ComparabilityBasis, *, now: datetime) -> Any:
        raise RuntimeError("secret-token-should-not-leak")


def test_manual_entry_returns_the_prices_staff_recorded() -> None:
    adapter = ManualEntryAdapter("ManualSite", [price(price_paise=4_500_000)])

    result = adapter.fetch(basis(), now=NOW)

    assert result.status is FetchStatus.OK
    assert result.site == "ManualSite"
    assert [item.price_paise for item in result.observations] == [4_500_000]


def test_manual_entry_refuses_an_entry_for_another_site() -> None:
    adapter = ManualEntryAdapter("ManualSite")

    with pytest.raises(ValueError, match="site"):
        adapter.add(price(site="OtherSite"))


def test_manual_entry_refuses_a_blocked_or_unavailable_entry() -> None:
    adapter = ManualEntryAdapter("ManualSite")

    with pytest.raises(ValueError, match="status"):
        adapter.add(price(status=ObservationStatus.BLOCKED))

    with pytest.raises(ValueError, match="status"):
        adapter.add(price(status=ObservationStatus.UNAVAILABLE))


def test_manual_entry_without_prices_reports_unavailable() -> None:
    result = ManualEntryAdapter("ManualSite").fetch(basis(), now=NOW)

    assert result.status is FetchStatus.UNAVAILABLE
    assert result.observations == ()
    assert "no manual" in result.detail


def test_the_licensed_data_adapter_is_a_stub_that_never_raises() -> None:
    adapter = LicensedDataAdapter()

    first = adapter.fetch(basis(), now=NOW)
    second = adapter.fetch(basis(), now=NOW)

    assert first.status is FetchStatus.UNAVAILABLE
    assert second.status is FetchStatus.UNAVAILABLE
    assert first.observations == ()
    assert "licensed" in first.detail


def test_a_naive_fetch_clock_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        ManualEntryAdapter("ManualSite").fetch(basis(), now=datetime(2026, 11, 1, 10, 0))


def test_a_failed_adapter_becomes_unavailable_without_leaking_its_message() -> None:
    fetches = fetch_competitor_prices([BrokenAdapter()], basis(), now=NOW)

    assert len(fetches) == 1
    assert fetches[0].status is FetchStatus.UNAVAILABLE
    assert fetches[0].observations == ()
    assert "RuntimeError" in fetches[0].detail
    assert "secret-token" not in fetches[0].detail


def test_one_failing_adapter_does_not_hide_another_sites_prices() -> None:
    manual = ManualEntryAdapter("ManualSite", [price(price_paise=4_500_000)])

    fetches = fetch_competitor_prices([BrokenAdapter(), manual], basis(), now=NOW)

    assert [fetch.status for fetch in fetches] == [
        FetchStatus.UNAVAILABLE,
        FetchStatus.OK,
    ]
    observations = competitor_observations(fetches)
    assert [item.site for item in observations] == ["ManualSite"]


def test_manual_prices_can_set_the_reference_even_when_another_site_fails() -> None:
    manual = ManualEntryAdapter(
        "ManualSite",
        [
            price(price_paise=4_500_000),
            price(price_paise=4_600_000, source_ref="manual/2"),
        ],
    )

    result = compare_competitor_prices(
        basis(),
        competitor_observations(
            fetch_competitor_prices([BrokenAdapter(), manual], basis(), now=NOW)
        ),
        now=NOW,
    )

    assert result.reference is not None
    assert result.reference.paise == 4_550_000
    assert ComparisonWarning.OUTLIERS_IGNORED not in result.warnings


def test_observations_flatten_in_adapter_order() -> None:
    first = ManualEntryAdapter("SiteA", [price(site="SiteA", price_paise=100_000)])
    second = ManualEntryAdapter("SiteB", [price(site="SiteB", price_paise=200_000)])

    observations = competitor_observations(
        fetch_competitor_prices([first, second], basis(), now=NOW)
    )

    assert [item.price_paise for item in observations] == [100_000, 200_000]


def test_manual_entry_needs_a_site_name() -> None:
    with pytest.raises(ValueError, match="site"):
        ManualEntryAdapter("   ")


def test_manual_entry_detail_reads_naturally_for_one_or_more_entries() -> None:
    single = ManualEntryAdapter("ManualSite", [price()])
    double = ManualEntryAdapter("ManualSite", [price(), price()])

    assert single.fetch(basis(), now=NOW).detail == "1 manual entry recorded"
    assert double.fetch(basis(), now=NOW).detail == "2 manual entries recorded"


def test_an_empty_adapter_list_is_not_an_error() -> None:
    assert fetch_competitor_prices([], basis(), now=NOW) == ()
    assert competitor_observations(()) == ()


def test_an_ok_fetch_must_carry_at_least_one_price() -> None:
    with pytest.raises(ValueError, match="at least one"):
        CompetitorFetch(
            site="ManualSite",
            status=FetchStatus.OK,
            fetched_at=NOW,
        )


@pytest.mark.parametrize("status", [FetchStatus.BLOCKED, FetchStatus.UNAVAILABLE])
def test_a_failed_fetch_cannot_carry_prices(status: FetchStatus) -> None:
    with pytest.raises(ValueError, match="must not carry"):
        CompetitorFetch(
            site="ManualSite",
            status=status,
            fetched_at=NOW,
            observations=(price(),),
        )


def test_a_fetch_needs_a_site_and_an_aware_time() -> None:
    with pytest.raises(ValueError, match="site"):
        CompetitorFetch(
            site="   ",
            status=FetchStatus.UNAVAILABLE,
            fetched_at=NOW,
        )

    with pytest.raises(ValueError, match="timezone-aware"):
        CompetitorFetch(
            site="ManualSite",
            status=FetchStatus.UNAVAILABLE,
            fetched_at=datetime(2026, 11, 1, 10, 0),
        )


def test_a_fetch_time_is_normalised_to_utc() -> None:
    ist = datetime(2026, 11, 1, 15, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))

    fetch = CompetitorFetch(site="ManualSite", status=FetchStatus.UNAVAILABLE, fetched_at=ist)

    assert fetch.fetched_at == NOW
    assert fetch.fetched_at.tzinfo is UTC
