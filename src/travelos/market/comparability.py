"""Score competitor prices and pick a trustworthy reference (task 014a1).

``assess_comparability`` answers one question: may this competitor price stand in
for the product we are pricing? Identity attributes (route, dates, party, fare
class, refundability) must match; baggage and unknown detail only reduce the
score. ``compare_competitor_prices`` then drops outliers and reports an exact
integer-paise reference (the median of the inliers) for the pricing engine's
ceiling.

Failure is normal here: a portal block, a missing page or a stale price leaves
``reference`` empty and attaches a warning. Nothing in this module raises because
a competitor did not answer, and nothing converts currency.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Final

from travelos.core.money import Money
from travelos.market.models import (
    AttributeComparison,
    ComparabilityAssessment,
    ComparabilityAttribute,
    ComparabilityBasis,
    ComparabilityLevel,
    ComparisonPolicy,
    ComparisonRejection,
    ComparisonWarning,
    CompetitorPrice,
    ObservationStatus,
)

DEFAULT_COMPARISON_POLICY: Final = ComparisonPolicy()

_LEVEL_UNITS: Final[dict[ComparabilityLevel, int]] = {
    ComparabilityLevel.EXACT: 2,
    ComparabilityLevel.CLOSE: 1,
    ComparabilityLevel.MISMATCH: 0,
    ComparabilityLevel.UNKNOWN: 0,
}

_FATAL_MISMATCHES: Final[dict[ComparabilityAttribute, ComparisonRejection]] = {
    ComparabilityAttribute.ROUTE: ComparisonRejection.ROUTE,
    ComparabilityAttribute.DATES: ComparisonRejection.DATES,
    ComparabilityAttribute.PAX: ComparisonRejection.PAX,
    ComparabilityAttribute.FARE_CLASS: ComparisonRejection.FARE_CLASS,
    ComparabilityAttribute.BAGGAGE: ComparisonRejection.BAGGAGE,
    ComparabilityAttribute.REFUNDABILITY: ComparisonRejection.REFUNDABILITY,
}


@dataclass(frozen=True, slots=True)
class TrustedObservation:
    """A comparable price that survived the outlier filter."""

    site: str
    price: Money
    fetched_at: datetime
    comparability: ComparabilityAssessment

    @property
    def price_paise(self) -> int:
        return self.price.paise


@dataclass(frozen=True, slots=True)
class RejectedObservation:
    """Why one observation may not influence the ceiling."""

    site: str
    reason: ComparisonRejection
    detail: str


@dataclass(frozen=True, slots=True)
class ComparisonResult:
    """The comparable price set, its reference and every refusal."""

    basis: ComparabilityBasis
    trusted: tuple[TrustedObservation, ...]
    outliers: tuple[TrustedObservation, ...]
    rejected: tuple[RejectedObservation, ...]
    reference: Money | None
    lowest: Money | None
    warnings: tuple[ComparisonWarning, ...]
    detail: str


def normalise_route(value: str) -> str:
    """Casefold and drop spacing around the separators competitors vary."""

    text = " ".join(value.casefold().split())
    for separator in ("\u2192", "\u2013", "\u2014"):
        text = text.replace(separator, "-")
    text = text.replace(" to ", "-")
    text = text.replace(" - ", "-").replace("- ", "-").replace(" -", "-")
    return text.replace(" / ", "/")


def median_paise(values: list[int] | tuple[int, ...]) -> int:
    """Exact integer median; an even count averages the middle pair, half-up."""

    if not values:
        raise ValueError("median of an empty sequence")
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle] + 1) // 2


def assess_comparability(
    basis: ComparabilityBasis,
    observation: CompetitorPrice,
    *,
    now: datetime,
    policy: ComparisonPolicy = DEFAULT_COMPARISON_POLICY,
) -> ComparabilityAssessment:
    """Decide whether one competitor price describes the same product."""

    moment = _aware_utc(now)
    refusal, detail = _refuse_before_scoring(basis, observation, moment, policy)
    if refusal is not None:
        return _refused(refusal, detail, policy)

    attributes = _compare_attributes(basis, observation, policy)
    score = _score(attributes, policy)
    unknown_weight = sum(
        policy.weights.weight(item.attribute)
        for item in attributes
        if item.level is ComparabilityLevel.UNKNOWN
    )

    for item in attributes:
        fatal = _FATAL_MISMATCHES.get(item.attribute)
        if fatal is not None and item.level is ComparabilityLevel.MISMATCH:
            return _refused(fatal, item.detail, policy, attributes=attributes, score=score)

    if unknown_weight > policy.max_unknown_weight:
        return _refused(
            ComparisonRejection.INSUFFICIENT_DETAIL,
            (
                f"{unknown_weight} of 100 points are unstated "
                f"(policy allows {policy.max_unknown_weight})"
            ),
            policy,
            attributes=attributes,
            score=score,
            unknown_weight=unknown_weight,
        )

    if score < policy.threshold:
        return _refused(
            ComparisonRejection.BELOW_THRESHOLD,
            f"comparability {score}/100 is below the threshold of {policy.threshold}",
            policy,
            attributes=attributes,
            score=score,
            unknown_weight=unknown_weight,
        )

    return ComparabilityAssessment(
        score=score,
        comparable=True,
        threshold=policy.threshold,
        unknown_weight=unknown_weight,
        attributes=attributes,
        rejection=None,
        detail=f"comparable at {score}/100 against a threshold of {policy.threshold}",
    )


def compare_competitor_prices(
    basis: ComparabilityBasis,
    observations: Sequence[CompetitorPrice],
    *,
    now: datetime,
    policy: ComparisonPolicy = DEFAULT_COMPARISON_POLICY,
) -> ComparisonResult:
    """Filter, outlier-check and summarise competitor prices for one basis."""

    moment = _aware_utc(now)
    trusted: list[TrustedObservation] = []
    rejected: list[RejectedObservation] = []

    for observation in observations:
        assessment = assess_comparability(basis, observation, now=moment, policy=policy)
        if assessment.comparable:
            trusted.append(
                TrustedObservation(
                    site=observation.site,
                    price=observation.price,
                    fetched_at=observation.fetched_at,
                    comparability=assessment,
                )
            )
            continue
        if assessment.rejection is None:  # pragma: no cover - model invariant
            raise AssertionError("a refused assessment must carry a rejection")
        rejected.append(
            RejectedObservation(
                site=observation.site,
                reason=assessment.rejection,
                detail=assessment.detail,
            )
        )

    trusted.sort(key=lambda item: (item.price_paise, item.site))
    rejected.sort(key=lambda item: (item.site, item.reason.value))

    if not trusted:
        return ComparisonResult(
            basis=basis,
            trusted=(),
            outliers=(),
            rejected=tuple(rejected),
            reference=None,
            lowest=None,
            warnings=(ComparisonWarning.NO_TRUSTED_PRICES,),
            detail=(
                f"no comparable competitor price from {len(observations)} observation(s); "
                "the ceiling must fall back to the markup cap"
            ),
        )

    prices = [item.price_paise for item in trusted]
    median = median_paise(prices)
    limit = _outlier_limit(prices, median, policy)
    inliers = tuple(item for item in trusted if abs(item.price_paise - median) <= limit)
    outliers = tuple(item for item in trusted if abs(item.price_paise - median) > limit)

    warnings: list[ComparisonWarning] = []
    if outliers:
        warnings.append(ComparisonWarning.OUTLIERS_IGNORED)
    if len(inliers) == 1:
        warnings.append(ComparisonWarning.SINGLE_SOURCE)

    inlier_prices = [item.price_paise for item in inliers]
    return ComparisonResult(
        basis=basis,
        trusted=inliers,
        outliers=outliers,
        rejected=tuple(rejected),
        reference=Money(median_paise(inlier_prices), basis.currency),
        lowest=Money(min(inlier_prices), basis.currency),
        warnings=tuple(warnings),
        detail=(
            f"{len(inliers)} of {len(observations)} competitor prices are comparable; "
            f"the reference is the median of {len(inliers)} inlier(s)"
            + (f" after ignoring {len(outliers)} outlier(s)" if outliers else "")
        ),
    )


def _refuse_before_scoring(
    basis: ComparabilityBasis,
    observation: CompetitorPrice,
    now: datetime,
    policy: ComparisonPolicy,
) -> tuple[ComparisonRejection | None, str]:
    if observation.status is ObservationStatus.BLOCKED:
        return ComparisonRejection.BLOCKED, "the portal returned a block"
    if observation.status is ObservationStatus.UNAVAILABLE:
        return ComparisonRejection.UNAVAILABLE, "the portal did not return a price"
    if basis.product_type != observation.product_type:
        return (
            ComparisonRejection.PRODUCT_TYPE,
            f"expected a {basis.product_type}, observed a {observation.product_type}",
        )
    if basis.currency != observation.currency:
        return (
            ComparisonRejection.CURRENCY,
            f"expected {basis.currency}, observed {observation.currency}",
        )
    age = now - observation.fetched_at
    if age < timedelta(0):
        return ComparisonRejection.STALE, "the observation claims a fetch time in the future"
    if age > policy.max_age:
        return (
            ComparisonRejection.STALE,
            f"observed {_hours(age)} ago; the maximum age is {_hours(policy.max_age)}",
        )
    return None, ""


def _compare_attributes(
    basis: ComparabilityBasis,
    observation: CompetitorPrice,
    policy: ComparisonPolicy,
) -> tuple[AttributeComparison, ...]:
    return (
        _compare_route(basis, observation),
        _compare_dates(basis, observation, policy),
        _compare_pax(basis, observation),
        _compare_fare_class(basis, observation),
        _compare_baggage(basis, observation),
        _compare_refundability(basis, observation),
    )


def _compare_route(basis: ComparabilityBasis, observation: CompetitorPrice) -> AttributeComparison:
    if normalise_route(basis.route) == normalise_route(observation.route):
        return AttributeComparison(
            attribute=ComparabilityAttribute.ROUTE,
            level=ComparabilityLevel.EXACT,
            detail=f"route {basis.route!r} matches",
        )
    return AttributeComparison(
        attribute=ComparabilityAttribute.ROUTE,
        level=ComparabilityLevel.MISMATCH,
        detail=f"route {basis.route!r} is not {observation.route!r}",
    )


def _compare_dates(
    basis: ComparabilityBasis,
    observation: CompetitorPrice,
    policy: ComparisonPolicy,
) -> AttributeComparison:
    attribute = ComparabilityAttribute.DATES
    if (basis.start_date, basis.end_date) == (
        observation.start_date,
        observation.end_date,
    ):
        return AttributeComparison(
            attribute=attribute, level=ComparabilityLevel.EXACT, detail="travel dates match"
        )
    if basis.end_date is None or observation.end_date is None:
        return AttributeComparison(
            attribute=attribute,
            level=ComparabilityLevel.MISMATCH,
            detail="one side has no return date, so the trips are different products",
        )
    basis_nights = (basis.end_date - basis.start_date).days
    observed_nights = (observation.end_date - observation.start_date).days
    start_gap = abs((observation.start_date - basis.start_date).days)
    end_gap = abs((observation.end_date - basis.end_date).days)
    if basis_nights == observed_nights and max(start_gap, end_gap) <= policy.date_tolerance_days:
        return AttributeComparison(
            attribute=attribute,
            level=ComparabilityLevel.CLOSE,
            detail=(
                f"travel dates shift by {max(start_gap, end_gap)} day(s) "
                f"with the same {basis_nights}-day length"
            ),
        )
    return AttributeComparison(
        attribute=attribute,
        level=ComparabilityLevel.MISMATCH,
        detail=(
            f"travel dates {basis.start_date}..{basis.end_date} are not "
            f"{observation.start_date}..{observation.end_date}"
        ),
    )


def _compare_pax(basis: ComparabilityBasis, observation: CompetitorPrice) -> AttributeComparison:
    attribute = ComparabilityAttribute.PAX
    if _party(basis) == _party(observation):
        return AttributeComparison(
            attribute=attribute,
            level=ComparabilityLevel.EXACT,
            detail=f"party matches ({_party(basis)})",
        )
    return AttributeComparison(
        attribute=attribute,
        level=ComparabilityLevel.MISMATCH,
        detail=f"party is {_party(basis)}; the competitor prices {_party(observation)}",
    )


def _compare_fare_class(
    basis: ComparabilityBasis, observation: CompetitorPrice
) -> AttributeComparison:
    attribute = ComparabilityAttribute.FARE_CLASS
    if basis.fare_class is None or observation.fare_class is None:
        return AttributeComparison(
            attribute=attribute,
            level=ComparabilityLevel.UNKNOWN,
            detail="fare class is not stated on both sides",
        )
    if basis.fare_class.casefold() == observation.fare_class.casefold():
        return AttributeComparison(
            attribute=attribute,
            level=ComparabilityLevel.EXACT,
            detail=f"fare class {basis.fare_class!r} matches",
        )
    return AttributeComparison(
        attribute=attribute,
        level=ComparabilityLevel.MISMATCH,
        detail=f"fare class {basis.fare_class!r} is not {observation.fare_class!r}",
    )


def _compare_baggage(
    basis: ComparabilityBasis, observation: CompetitorPrice
) -> AttributeComparison:
    attribute = ComparabilityAttribute.BAGGAGE
    if basis.checked_bags is None or observation.checked_bags is None:
        return AttributeComparison(
            attribute=attribute,
            level=ComparabilityLevel.UNKNOWN,
            detail="checked baggage is not stated on both sides",
        )
    difference = abs(basis.checked_bags - observation.checked_bags)
    detail = (
        f"checked baggage is {_bags(basis.checked_bags)} versus {_bags(observation.checked_bags)}"
    )
    if difference == 0:
        return AttributeComparison(
            attribute=attribute, level=ComparabilityLevel.EXACT, detail=detail
        )
    if difference == 1:
        return AttributeComparison(
            attribute=attribute, level=ComparabilityLevel.CLOSE, detail=detail
        )
    return AttributeComparison(
        attribute=attribute, level=ComparabilityLevel.MISMATCH, detail=detail
    )


def _compare_refundability(
    basis: ComparabilityBasis, observation: CompetitorPrice
) -> AttributeComparison:
    attribute = ComparabilityAttribute.REFUNDABILITY
    if basis.refundable is None or observation.refundable is None:
        return AttributeComparison(
            attribute=attribute,
            level=ComparabilityLevel.UNKNOWN,
            detail="refundability is not stated on both sides",
        )
    label = "refundable" if basis.refundable else "non-refundable"
    if basis.refundable == observation.refundable:
        return AttributeComparison(
            attribute=attribute, level=ComparabilityLevel.EXACT, detail=f"both fares are {label}"
        )
    return AttributeComparison(
        attribute=attribute,
        level=ComparabilityLevel.MISMATCH,
        detail=f"our fare is {label}; the competitor page says otherwise",
    )


def _score(attributes: tuple[AttributeComparison, ...], policy: ComparisonPolicy) -> int:
    units = sum(
        policy.weights.weight(item.attribute) * _LEVEL_UNITS[item.level] for item in attributes
    )
    return (units + 1) // 2


def _outlier_limit(prices: list[int], median: int, policy: ComparisonPolicy) -> int:
    deviation = median_paise([abs(value - median) for value in prices])
    limit = policy.mad_multiplier * deviation
    if limit == 0:
        return median * policy.relative_outlier_floor_bps // 10_000
    return limit


def _refused(
    rejection: ComparisonRejection,
    detail: str,
    policy: ComparisonPolicy,
    *,
    attributes: tuple[AttributeComparison, ...] = (),
    score: int = 0,
    unknown_weight: int = 0,
) -> ComparabilityAssessment:
    return ComparabilityAssessment(
        score=score,
        comparable=False,
        threshold=policy.threshold,
        unknown_weight=unknown_weight,
        attributes=attributes,
        rejection=rejection,
        detail=detail,
    )


def _party(shape: ComparabilityBasis | CompetitorPrice) -> str:
    return ", ".join(
        (
            _count(shape.adults, "adult"),
            _count(shape.children, "child", "children"),
            _count(shape.infants, "infant"),
        )
    )


def _count(value: int, singular: str, plural: str | None = None) -> str:
    return f"{value} {singular if value == 1 else (plural or singular + 's')}"


def _bags(value: int) -> str:
    return _count(value, "bag")


def _hours(delta: timedelta) -> str:
    minutes = delta // timedelta(minutes=1)
    return f"{minutes // 60} h {minutes % 60} min"


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)
