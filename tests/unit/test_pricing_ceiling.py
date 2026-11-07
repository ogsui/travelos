"""Trusted competitor ceiling tests (task 015d).

`compare_competitor_prices` already scores and de-noises competitor prices; this
slice binds it to the owner's ``CompetitorPolicy`` so the quote uses the
rule-set threshold and maximum age. Every value here is a synthetic engineering
fixture, not owner policy or a real competitor price.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

from travelos.core.money import money
from travelos.market.models import ComparabilityBasis, CompetitorPrice
from travelos.pricing.ceiling import trusted_ceiling
from travelos.pricing.models import CompetitorPolicy

NOW = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
START = date(2026, 12, 10)
END = date(2026, 12, 15)


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


def observation(**overrides: Any) -> CompetitorPrice:
    values: dict[str, Any] = {
        "site": "SiteA",
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
        "fetched_at": NOW - timedelta(minutes=30),
    }
    values.update(overrides)
    return CompetitorPrice.model_validate(values)


def policy(**overrides: Any) -> CompetitorPolicy:
    values: dict[str, Any] = {
        "max_age": timedelta(hours=24),
        "min_comparability": 80,
        "undercut_paise": 10_000,
    }
    values.update(overrides)
    return CompetitorPolicy(**values)


def test_one_comparable_observation_sets_the_pre_undercut_reference() -> None:
    ceiling = trusted_ceiling(
        basis(), (observation(price_paise=4_500_000),), policy=policy(), now=NOW
    )

    assert ceiling.reference == money(4_500_000)
    assert ceiling.comparison.rejected == ()
    assert "single_source" in ceiling.warnings
    assert "1 of 1" in ceiling.detail
    assert "before the undercut" in ceiling.detail


def test_no_observations_falls_back_without_a_reference() -> None:
    ceiling = trusted_ceiling(basis(), (), policy=policy(), now=NOW)

    assert ceiling.reference is None
    assert ceiling.usable is False
    assert "no_trusted_prices" in ceiling.warnings
    assert "markup cap" in ceiling.detail


def test_the_maximum_age_comes_from_the_rule_set() -> None:
    stale = observation(fetched_at=NOW - timedelta(hours=2))

    rejected = trusted_ceiling(
        basis(), (stale,), policy=policy(max_age=timedelta(hours=1)), now=NOW
    )
    accepted = trusted_ceiling(
        basis(), (stale,), policy=policy(max_age=timedelta(hours=24)), now=NOW
    )

    assert rejected.reference is None
    assert accepted.reference == money(4_500_000)


def test_the_threshold_comes_from_the_rule_set() -> None:
    unstated = basis(fare_class=None, checked_bags=None)
    observed = observation(fare_class=None, checked_bags=None)

    strict = trusted_ceiling(unstated, (observed,), policy=policy(min_comparability=81), now=NOW)
    lenient = trusted_ceiling(unstated, (observed,), policy=policy(min_comparability=80), now=NOW)

    assert strict.reference is None
    assert [item.reason for item in strict.comparison.rejected] == ["below_threshold"]
    assert "threshold" in strict.comparison.rejected[0].detail
    assert lenient.reference == money(4_500_000)


def test_an_outlier_is_ignored_and_the_inlier_median_is_the_reference() -> None:
    ceiling = trusted_ceiling(
        basis(),
        (
            observation(site="SiteA", price_paise=4_500_000),
            observation(site="SiteB", price_paise=4_510_000),
            observation(site="SiteC", price_paise=4_520_000),
            observation(site="SiteD", price_paise=9_000_000),
        ),
        policy=policy(),
        now=NOW,
    )

    assert ceiling.reference == money(4_510_000)
    assert len(ceiling.comparison.outliers) == 1
    assert ceiling.comparison.outliers[0].site == "SiteD"
    assert "outliers_ignored" in ceiling.warnings


def test_a_non_inr_basis_can_never_set_the_inr_ceiling() -> None:
    ceiling = trusted_ceiling(
        basis(currency="USD"),
        (observation(currency="USD", price_paise=120_000),),
        policy=policy(),
        now=NOW,
    )

    assert ceiling.reference is None
    assert "INR" in ceiling.detail
    assert any("competitor.currency" in warning for warning in ceiling.warnings)


def test_a_mismatched_observation_currency_is_refused() -> None:
    ceiling = trusted_ceiling(
        basis(),
        (observation(currency="USD"),),
        policy=policy(),
        now=NOW,
    )

    assert ceiling.reference is None
    assert [item.reason for item in ceiling.comparison.rejected] == ["currency_mismatch"]


def test_a_single_trusted_source_is_reported_as_a_warning() -> None:
    ceiling = trusted_ceiling(basis(), (observation(),), policy=policy(), now=NOW)

    assert ceiling.reference is not None
    assert "single_source" in ceiling.warnings
