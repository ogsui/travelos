"""Bind competitor observations to the quote's trusted ceiling (task 015d).

``compare_competitor_prices`` (014a1) already scores, filters and de-noises
observations. This module binds that work to the owner's ``CompetitorPolicy``:
the rule-set ``min_comparability`` and ``max_age`` decide what is trusted, and
the result is only usable when it is in INR because the calculator never mixes
currencies.

The reference is the median of the inlier prices before the owner's undercut;
``price_quote`` subtracts the undercut. Nothing is converted here and no policy
default is invented: the caller passes the active rule set and the observations
it wants considered (the quote slice reads them from the market store).

No I/O, clock or LLM access.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime

from travelos.core.money import Money
from travelos.market.comparability import ComparisonResult, compare_competitor_prices
from travelos.market.models import (
    ComparabilityBasis,
    ComparisonPolicy,
    CompetitorPrice,
)
from travelos.pricing.models import CompetitorPolicy

_INR = "INR"


@dataclass(frozen=True, slots=True)
class TrustedCeiling:
    """The pre-undercut competitor reference and why it was chosen.

    ``reference`` is the only value a quote may use. ``comparison`` is kept for
    review, but its own ``reference`` may be non-INR; it is never passed on.
    """

    reference: Money | None
    comparison: ComparisonResult
    warnings: tuple[str, ...]
    detail: str

    @property
    def usable(self) -> bool:
        return self.reference is not None


def trusted_ceiling(
    basis: ComparabilityBasis,
    observations: Sequence[CompetitorPrice],
    *,
    policy: CompetitorPolicy,
    now: datetime,
) -> TrustedCeiling:
    """Return the trusted reference for one product, or an explained fallback.

    The comparison policy is derived from the owner's rule set; the remaining
    comparison weights are engineering defaults recorded in
    ``docs/BACKLOG.md`` until the owner confirms them. A failed comparison is
    normal and never raises: the quote falls back to the markup cap.
    """

    comparison = compare_competitor_prices(
        basis,
        observations,
        now=now,
        policy=ComparisonPolicy(
            threshold=policy.min_comparability,
            max_age=policy.max_age,
        ),
    )
    warnings = [warning.value for warning in comparison.warnings]
    reference = comparison.reference
    detail = comparison.detail

    if basis.currency != _INR:
        reference = None
        detail = (
            f"the comparability basis is priced in {basis.currency}; the quote ceiling must be INR"
        )
        warnings.append(
            f"competitor.currency: {basis.currency} observations cannot set an INR ceiling"
        )
    elif reference is not None and reference.currency != _INR:
        currency = reference.currency
        reference = None
        detail = f"the competitor reference is in {currency}; the quote ceiling must be INR"
        warnings.append(f"competitor.currency: {currency} cannot set an INR ceiling")
    elif reference is not None:
        detail = (
            f"{comparison.detail}; the reference is {reference.paise} paise before the undercut"
        )

    return TrustedCeiling(
        reference=(None if reference is None else Money(reference.paise, reference.currency)),
        comparison=comparison,
        warnings=tuple(dict.fromkeys(warnings)),
        detail=detail,
    )


__all__ = ["TrustedCeiling", "trusted_ceiling"]
