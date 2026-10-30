"""Competitor price adapters: manual entry and a licensed-data stub (task 014a2a).

An adapter answers one question for one site: what retail prices does this site
show for this product, right now? Nothing here uses the network, a database or an
LLM. Manual entry is the dashboard hook for prices a human recorded after the
owner authorized the site; the licensed-data adapter is a deliberate stub until an
owner-supplied provider contract exists (``TODO(owner)``).

The contract is deliberately narrow:

* an adapter reports a ``CompetitorFetch`` for every call, including failure, so
  one broken site cannot hide another site's prices (MKT-3);
* an exception is turned into ``unavailable`` with only the exception *type name*,
  never its message, because provider errors can echo tokens or account details;
* the caller supplies ``now``, so runs are deterministic and testable.

Portal automation, session handling and challenge/OTP flows are task 014b and are
blocked on the owner's per-portal authorization. No code in this module opens a
browser, stores a credential or changes any state on a provider.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime

from travelos.market.models import (
    ComparabilityBasis,
    CompetitorPrice,
    FetchStatus,
    ObservationStatus,
)


@dataclass(frozen=True, slots=True)
class CompetitorFetch:
    """One adapter run: its site, outcome, prices and a staff-readable reason.

    ``observations`` is empty unless ``status`` is ``ok``. A blocked portal keeps
    its own status; an adapter that raised becomes ``unavailable`` with a detail
    that names the exception type only.
    """

    site: str
    status: FetchStatus
    fetched_at: datetime
    observations: tuple[CompetitorPrice, ...] = ()
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.site.strip():
            raise ValueError("site must not be blank")
        if self.fetched_at.tzinfo is None or self.fetched_at.utcoffset() is None:
            raise ValueError("fetched_at must be timezone-aware")
        object.__setattr__(self, "fetched_at", self.fetched_at.astimezone(UTC))
        if self.status is FetchStatus.OK and not self.observations:
            raise ValueError("an ok fetch must carry at least one observation")
        if self.status is not FetchStatus.OK and self.observations:
            raise ValueError("a failed fetch must not carry observations")


class CompetitorPriceAdapter(ABC):
    """The interface every competitor-price source implements."""

    @property
    @abstractmethod
    def site(self) -> str:
        """Stable site name, used to group and order prices."""

    @abstractmethod
    def fetch(self, basis: ComparabilityBasis, *, now: datetime) -> CompetitorFetch:
        """Return this site's prices for ``basis``, or why it has none."""


class ManualEntryAdapter(CompetitorPriceAdapter):
    """Prices a staff member read on an authorized site and typed in.

    This is the only source that works without a provider contract. Entries are
    appended one at a time by the dashboard and are never edited in place: a
    correction is a new observation with its own fetch time, so the comparison
    keeps its history. Every stored entry is returned on each fetch; deciding
    which ones are still fresh enough is the comparability step's job.
    """

    __slots__ = ("_prices", "_site")

    _site: str
    _prices: list[CompetitorPrice]

    def __init__(self, site: str, prices: Sequence[CompetitorPrice] | None = None) -> None:
        if not site.strip():
            raise ValueError("site must not be blank")
        self._site = site
        self._prices = []
        for price in prices or ():
            self.add(price)

    @property
    def site(self) -> str:
        return self._site

    def add(self, price: CompetitorPrice) -> None:
        """Record one manually copied price for this site."""

        if price.site != self._site:
            raise ValueError(f"entry belongs to site {price.site!r}, not {self._site!r}")
        if price.status is not ObservationStatus.OK:
            raise ValueError(
                f"only an ok observation can be entered manually; got status {price.status.value!r}"
            )
        self._prices.append(price)

    def fetch(self, basis: ComparabilityBasis, *, now: datetime) -> CompetitorFetch:
        del basis  # manual prices are filtered by the comparability step
        moment = _aware_utc(now)
        if not self._prices:
            return CompetitorFetch(
                site=self._site,
                status=FetchStatus.UNAVAILABLE,
                fetched_at=moment,
                detail=f"no manual entries recorded for {self._site}",
            )
        return CompetitorFetch(
            site=self._site,
            status=FetchStatus.OK,
            fetched_at=moment,
            observations=tuple(self._prices),
            detail=(
                f"{len(self._prices)} manual "
                f"{'entry' if len(self._prices) == 1 else 'entries'} recorded"
            ),
        )


@dataclass(frozen=True, slots=True)
class LicensedDataAdapter(CompetitorPriceAdapter):
    """Placeholder for a paid market-data feed.

    It stays unavailable until the owner supplies the provider's official docs
    and contract terms. Faking a response here would put invented prices into a
    customer quote, so the stub always reports ``unavailable``.
    """

    site: str = "LicensedData"

    def fetch(self, basis: ComparabilityBasis, *, now: datetime) -> CompetitorFetch:
        del basis
        return CompetitorFetch(
            site=self.site,
            status=FetchStatus.UNAVAILABLE,
            fetched_at=_aware_utc(now),
            detail=(
                "licensed data provider is not configured; "
                "TODO(owner): name the provider and supply its official API docs"
            ),
        )


def fetch_competitor_prices(
    adapters: Sequence[CompetitorPriceAdapter],
    basis: ComparabilityBasis,
    *,
    now: datetime,
) -> tuple[CompetitorFetch, ...]:
    """Run every adapter once, in order, and never let one failure stop the rest.

    An adapter that raises — a timeout, a parser bug, a provider 5xx — becomes an
    ``unavailable`` fetch. Only the exception type name is kept: messages can
    contain URLs with session tokens and must not reach logs or staff screens.
    """

    moment = _aware_utc(now)
    fetches: list[CompetitorFetch] = []
    for adapter in adapters:
        try:
            fetches.append(adapter.fetch(basis, now=moment))
        except Exception as exc:
            fetches.append(
                CompetitorFetch(
                    site=adapter.site,
                    status=FetchStatus.UNAVAILABLE,
                    fetched_at=moment,
                    detail=f"adapter failed ({type(exc).__name__})",
                )
            )
    return tuple(fetches)


def competitor_observations(
    fetches: Sequence[CompetitorFetch],
) -> tuple[CompetitorPrice, ...]:
    """Flatten the prices from successful fetches, keeping adapter order."""

    return tuple(
        observation
        for fetch in fetches
        if fetch.status is FetchStatus.OK
        for observation in fetch.observations
    )


def _aware_utc(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    return value.astimezone(UTC)
