"""Competitor price comparability and reference selection (task 014a1).

These rules decide whether a competitor's retail price may influence our price
ceiling. Route, travel dates, party, fare class and refundability are identity
dimensions: a mismatch means a different product. Baggage allowance is graded,
and an observation whose detail is mostly unknown is refused rather than
trusted.

The defaults are engineering placeholders pending owner confirmation (see
docs/BACKLOG.md): a comparability threshold of 80, at most 20 points of unknown
detail, a one-day date tolerance, a 24-hour maximum age and a three-MAD outlier
rule with a 10% floor.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from travelos.market.comparability import (
    ComparisonPolicy,
    assess_comparability,
    compare_competitor_prices,
    median_paise,
    normalise_route,
)
from travelos.market.models import (
    ComparabilityAttribute,
    ComparabilityBasis,
    ComparabilityLevel,
    ComparisonRejection,
    ComparisonWarning,
    CompetitorPrice,
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
        "children": 0,
        "infants": 0,
        "fare_class": "economy",
        "checked_bags": 1,
        "refundable": True,
        "currency": "INR",
    }
    values.update(overrides)
    return ComparabilityBasis.model_validate(values)


def price(**overrides: Any) -> CompetitorPrice:
    values: dict[str, Any] = {
        "site": "MockCompetitor",
        "product_type": "flight",
        "route": "Delhi-Goa",
        "start_date": START,
        "end_date": END,
        "adults": 2,
        "children": 0,
        "infants": 0,
        "fare_class": "economy",
        "checked_bags": 1,
        "refundable": True,
        "price_paise": 4_500_000,
        "currency": "INR",
        "fetched_at": NOW - timedelta(minutes=5),
        "status": ObservationStatus.OK,
        "source_ref": "fixture://mock/1",
    }
    values.update(overrides)
    return CompetitorPrice.model_validate(values)


def level(assessment: Any, attribute: ComparabilityAttribute) -> ComparabilityLevel:
    return next(item.level for item in assessment.attributes if item.attribute is attribute)


def test_an_identical_observation_scores_full_marks() -> None:
    assessment = assess_comparability(basis(), price(), now=NOW)

    assert assessment.score == 100
    assert assessment.comparable is True
    assert assessment.rejection is None
    assert assessment.unknown_weight == 0
    assert len(assessment.attributes) == 6


@pytest.mark.parametrize(
    ("written", "expected"),
    [
        (" delhi  -  GOA ", "delhi-goa"),
        ("Delhi → Goa", "delhi-goa"),
        ("Delhi to Goa", "delhi-goa"),
        ("Delhi\u2013Goa", "delhi-goa"),
    ],
)
def test_route_matching_ignores_case_spacing_and_separator_style(
    written: str, expected: str
) -> None:
    assert normalise_route(written) == expected


def test_a_reversed_route_is_a_different_product() -> None:
    assessment = assess_comparability(basis(), price(route="Goa-Delhi"), now=NOW)

    assert assessment.comparable is False
    assert assessment.rejection is ComparisonRejection.ROUTE
    assert level(assessment, ComparabilityAttribute.ROUTE) is ComparabilityLevel.MISMATCH


def test_product_type_mismatch_is_rejected() -> None:
    assessment = assess_comparability(basis(), price(product_type="hotel"), now=NOW)

    assert assessment.rejection is ComparisonRejection.PRODUCT_TYPE


def test_a_foreign_currency_is_rejected_without_conversion() -> None:
    assessment = assess_comparability(basis(), price(currency="USD"), now=NOW)

    assert assessment.rejection is ComparisonRejection.CURRENCY


def test_a_price_older_than_the_maximum_age_is_stale() -> None:
    stale = assess_comparability(basis(), price(fetched_at=NOW - timedelta(hours=25)), now=NOW)
    fresh = assess_comparability(
        basis(), price(fetched_at=NOW - timedelta(hours=23, minutes=59)), now=NOW
    )

    assert stale.rejection is ComparisonRejection.STALE
    assert fresh.comparable is True


def test_a_fetch_time_in_the_future_is_not_trusted() -> None:
    assessment = assess_comparability(basis(), price(fetched_at=NOW + timedelta(hours=1)), now=NOW)

    assert assessment.rejection is ComparisonRejection.STALE


def test_the_maximum_age_is_configurable() -> None:
    policy = ComparisonPolicy(max_age=timedelta(hours=1))
    observed = price(fetched_at=NOW - timedelta(hours=2))

    assessment = assess_comparability(basis(), observed, now=NOW, policy=policy)

    assert assessment.rejection is ComparisonRejection.STALE


def test_a_blocked_portal_result_is_recorded_not_raised() -> None:
    blocked = assess_comparability(basis(), price(status=ObservationStatus.BLOCKED), now=NOW)
    unavailable = assess_comparability(
        basis(), price(status=ObservationStatus.UNAVAILABLE), now=NOW
    )

    assert blocked.rejection is ComparisonRejection.BLOCKED
    assert unavailable.rejection is ComparisonRejection.UNAVAILABLE


def test_a_one_day_shift_with_the_same_duration_is_close() -> None:
    assessment = assess_comparability(
        basis(),
        price(start_date=START + timedelta(days=1), end_date=END + timedelta(days=1)),
        now=NOW,
    )

    assert assessment.comparable is True
    assert assessment.score == 88
    assert level(assessment, ComparabilityAttribute.DATES) is ComparabilityLevel.CLOSE


def test_a_two_day_shift_is_not_comparable() -> None:
    assessment = assess_comparability(
        basis(),
        price(start_date=START + timedelta(days=2), end_date=END + timedelta(days=2)),
        now=NOW,
    )

    assert assessment.rejection is ComparisonRejection.DATES


def test_a_shift_that_changes_the_stay_length_is_not_comparable() -> None:
    assessment = assess_comparability(
        basis(),
        price(start_date=START + timedelta(days=1), end_date=END + timedelta(days=2)),
        now=NOW,
    )

    assert assessment.rejection is ComparisonRejection.DATES


def test_one_way_and_return_are_not_comparable() -> None:
    assessment = assess_comparability(basis(), price(end_date=None), now=NOW)

    assert assessment.rejection is ComparisonRejection.DATES


def test_the_traveller_party_must_match_exactly() -> None:
    assessment = assess_comparability(basis(), price(adults=3), now=NOW)

    assert assessment.rejection is ComparisonRejection.PAX


def test_a_different_fare_class_is_not_comparable() -> None:
    assessment = assess_comparability(basis(), price(fare_class="business"), now=NOW)

    assert assessment.rejection is ComparisonRejection.FARE_CLASS


def test_an_unstated_fare_class_lowers_the_score_but_is_usable() -> None:
    assessment = assess_comparability(basis(), price(fare_class=None), now=NOW)

    assert assessment.comparable is True
    assert assessment.score == 90
    assert assessment.unknown_weight == 10
    assert level(assessment, ComparabilityAttribute.FARE_CLASS) is ComparabilityLevel.UNKNOWN


def test_an_unstated_refundability_lowers_the_score_but_is_usable() -> None:
    assessment = assess_comparability(basis(), price(refundable=None), now=NOW)

    assert assessment.comparable is True
    assert assessment.score == 90


def test_a_refundability_mismatch_is_not_comparable() -> None:
    assessment = assess_comparability(basis(), price(refundable=False), now=NOW)

    assert assessment.rejection is ComparisonRejection.REFUNDABILITY


def test_one_extra_checked_bag_is_a_close_match() -> None:
    assessment = assess_comparability(basis(), price(checked_bags=2), now=NOW)

    assert assessment.comparable is True
    assert assessment.score == 95
    assert level(assessment, ComparabilityAttribute.BAGGAGE) is ComparabilityLevel.CLOSE


def test_a_much_different_baggage_allowance_is_graded_down() -> None:
    assessment = assess_comparability(basis(), price(checked_bags=3), now=NOW)

    assert assessment.comparable is True
    assert assessment.score == 90
    assert level(assessment, ComparabilityAttribute.BAGGAGE) is ComparabilityLevel.MISMATCH


def test_too_much_unknown_detail_is_refused() -> None:
    assessment = assess_comparability(
        basis(),
        price(fare_class=None, checked_bags=None, refundable=None),
        now=NOW,
    )

    assert assessment.unknown_weight == 30
    assert assessment.rejection is ComparisonRejection.INSUFFICIENT_DETAIL


def test_the_threshold_is_configurable() -> None:
    policy = ComparisonPolicy(threshold=95)

    assessment = assess_comparability(basis(), price(fare_class=None), now=NOW, policy=policy)

    assert assessment.score == 90
    assert assessment.rejection is ComparisonRejection.BELOW_THRESHOLD


def test_weights_must_total_one_hundred() -> None:
    with pytest.raises(ValueError, match="total 100"):
        ComparisonPolicy.model_validate(
            {
                "weights": {
                    "route": 30,
                    "dates": 30,
                    "pax": 15,
                    "fare_class": 10,
                    "baggage": 10,
                    "refundability": 10,
                }
            }
        )


def test_the_maximum_age_must_be_positive() -> None:
    with pytest.raises(ValueError, match="positive"):
        ComparisonPolicy(max_age=timedelta(0))


def test_a_one_way_trip_matches_another_one_way_trip() -> None:
    one_way = basis(end_date=None)

    assessment = assess_comparability(one_way, price(end_date=None), now=NOW)

    assert assessment.comparable is True
    assert assessment.score == 100


def test_scoring_is_deterministic() -> None:
    first = assess_comparability(basis(), price(), now=NOW)
    second = assess_comparability(basis(), price(), now=NOW)

    assert first == second


@pytest.mark.parametrize(
    ("values", "expected"),
    [([3, 1, 2], 2), ([10, 20], 15), ([10, 21], 16), ([7], 7)],
)
def test_median_is_exact_integer_paise(values: list[int], expected: int) -> None:
    assert median_paise(values) == expected


def test_median_requires_a_value() -> None:
    with pytest.raises(ValueError, match="empty"):
        median_paise([])


def test_the_reference_is_the_median_of_comparable_prices() -> None:
    result = compare_competitor_prices(
        basis(),
        [
            price(site="SiteA", price_paise=102_000),
            price(site="SiteB", price_paise=100_000),
            price(site="SiteC", price_paise=101_000),
        ],
        now=NOW,
    )

    assert result.reference is not None
    assert result.reference.paise == 101_000
    assert result.lowest is not None
    assert result.lowest.paise == 100_000
    assert len(result.trusted) == 3
    assert result.outliers == ()
    assert result.warnings == ()


def test_an_outlier_never_moves_the_reference() -> None:
    result = compare_competitor_prices(
        basis(),
        [
            price(site="SiteA", price_paise=100_000),
            price(site="SiteB", price_paise=100_000),
            price(site="SiteC", price_paise=100_000),
            price(site="SiteD", price_paise=500_000),
        ],
        now=NOW,
    )

    assert result.reference is not None
    assert result.reference.paise == 100_000
    assert [item.price_paise for item in result.outliers] == [500_000]
    assert ComparisonWarning.OUTLIERS_IGNORED in result.warnings


def test_trusted_prices_are_ordered_by_price_then_site() -> None:
    result = compare_competitor_prices(
        basis(),
        [
            price(site="Zeta", price_paise=100_000),
            price(site="Beta", price_paise=100_000),
            price(site="Alpha", price_paise=100_000),
        ],
        now=NOW,
    )

    assert [item.site for item in result.trusted] == ["Alpha", "Beta", "Zeta"]


def test_a_single_source_is_flagged() -> None:
    result = compare_competitor_prices(basis(), [price()], now=NOW)

    assert result.reference is not None
    assert ComparisonWarning.SINGLE_SOURCE in result.warnings


def test_only_rejected_observations_leave_no_reference_and_no_exception() -> None:
    result = compare_competitor_prices(
        basis(),
        [
            price(site="Blocked", status=ObservationStatus.BLOCKED),
            price(site="Unavailable", status=ObservationStatus.UNAVAILABLE),
            price(site="WrongRoute", route="Mumbai-Goa"),
        ],
        now=NOW,
    )

    assert result.reference is None
    assert result.lowest is None
    assert result.trusted == ()
    assert len(result.rejected) == 3
    assert ComparisonWarning.NO_TRUSTED_PRICES in result.warnings


def test_no_observations_are_not_an_error() -> None:
    result = compare_competitor_prices(basis(), [], now=NOW)

    assert result.reference is None
    assert result.rejected == ()
    assert ComparisonWarning.NO_TRUSTED_PRICES in result.warnings


def test_rejections_are_ordered_deterministically() -> None:
    result = compare_competitor_prices(
        basis(),
        [
            price(site="Zulu", status=ObservationStatus.BLOCKED),
            price(site="Alpha", route="Mumbai-Goa"),
        ],
        now=NOW,
    )

    assert [item.site for item in result.rejected] == ["Alpha", "Zulu"]


def test_a_naive_clock_is_refused() -> None:
    with pytest.raises(ValueError, match="timezone-aware"):
        assess_comparability(basis(), price(), now=datetime(2026, 11, 1, 10, 0))


@given(
    st.lists(
        st.integers(min_value=0, max_value=10_000_000),
        min_size=1,
        max_size=8,
    )
)
def test_the_reference_is_always_inside_the_observed_range(prices: list[int]) -> None:
    result = compare_competitor_prices(
        basis(),
        [price(site=f"Site{index}", price_paise=value) for index, value in enumerate(prices)],
        now=NOW,
    )

    assert result.reference is not None
    assert result.lowest is not None
    assert min(prices) <= result.lowest.paise <= result.reference.paise <= max(prices)


@given(
    st.lists(
        st.integers(min_value=0, max_value=10_000_000),
        min_size=1,
        max_size=5,
    )
)
def test_comparison_is_deterministic_for_the_same_input(prices: list[int]) -> None:
    observations = [
        price(site=f"Site{index}", price_paise=value) for index, value in enumerate(prices)
    ]

    first = compare_competitor_prices(basis(), observations, now=NOW)
    second = compare_competitor_prices(basis(), observations, now=NOW)

    assert first == second
