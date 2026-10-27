"""Parallel supplier search, timeouts, cache and circuit breaker (task 012a2)."""

from __future__ import annotations

import threading
import time
from datetime import UTC, date, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.core.audit import AuditWriter
from travelos.core.clock import ManualClock
from travelos.core.models import AuditLog
from travelos.suppliers.errors import (
    SupplierTimeoutError,
    SupplierUnavailableError,
    SupplierValidationError,
)
from travelos.suppliers.models import (
    CancellationRules,
    FareRules,
    Offer,
    SearchLeg,
    SearchResult,
    SupplierSearchQuery,
)
from travelos.suppliers.records import PriceSnapshot, Supplier, SupplierSearch
from travelos.suppliers.search import SupplierSearchService, request_hash

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
QUERY = SupplierSearchQuery(
    legs=(
        SearchLeg(
            product_type="flight",
            route="Mumbai-Goa",
            travel_date=date(2026, 12, 1),
        ),
    )
)


def make_offer(
    *,
    supplier: str = "MockAir",
    offer_id: str | None = None,
    fetched_at: datetime = NOW,
) -> Offer:
    return Offer(
        supplier=supplier,
        offer_id=offer_id or f"sku_{supplier.lower()}",
        product_type="flight",
        name=f"{supplier} Mumbai to Goa",
        city="Goa",
        route="Mumbai-Goa",
        currency="INR",
        net_paise=3_000_000,
        taxes_paise=150_000,
        available_count=8,
        fare_rules=FareRules(
            cancellation=CancellationRules(
                refundable=True,
                cancellation_days=7,
                change_fee_paise=0,
            )
        ),
        fetched_at=fetched_at,
        expires_at=fetched_at + timedelta(minutes=10),
    )


class FakeAdapter:
    """A supplier adapter with controllable failures and timing."""

    def __init__(
        self,
        name: str,
        *,
        offers: tuple[Offer, ...] = (),
        error: Exception | None = None,
        delay: float = 0.0,
        barrier: threading.Barrier | None = None,
    ) -> None:
        self.name = name
        self._offers = offers
        self._error = error
        self._delay = delay
        self._barrier = barrier
        self.calls = 0

    def fail_with(self, error: Exception | None) -> None:
        self._error = error

    def offer(self, *offers: Offer) -> None:
        self._offers = offers

    def search(self, query: SupplierSearchQuery) -> SearchResult:
        del query
        self.calls += 1
        if self._barrier is not None:
            self._barrier.wait(timeout=2)
        if self._delay:
            time.sleep(self._delay)
        if self._error is not None:
            raise self._error
        return SearchResult(
            supplier=self.name,
            offers=self._offers,
            fetched_at=NOW,
        )


def service(
    *adapters: FakeAdapter,
    clock: ManualClock | None = None,
    timeout: timedelta = timedelta(seconds=2),
    threshold: int = 3,
    reset_after: timedelta = timedelta(seconds=60),
    ttl: timedelta = timedelta(minutes=10),
) -> SupplierSearchService:
    return SupplierSearchService(
        adapters,
        clock=clock or ManualClock(NOW),
        timeout=timeout,
        circuit_failure_threshold=threshold,
        circuit_reset_after=reset_after,
        snapshot_ttl=ttl,
        audit=AuditWriter(),
    )


def audit_actions(db: Session) -> list[str]:
    db.flush()
    return [row[0] for row in db.execute(select(AuditLog.action).order_by(AuditLog.at)).all()]


# ---------------------------------------------------------------------------
# Partial results, failures and timeouts
# ---------------------------------------------------------------------------


def test_one_failed_supplier_returns_partial_results_with_a_flag(db: Session) -> None:
    good = FakeAdapter("MockAir", offers=(make_offer(offer_id="sku_air"),))
    blocked = FakeAdapter(
        "MockPortal",
        error=SupplierUnavailableError("MockPortal", "access denied"),
    )

    outcome = service(good, blocked).search(db, QUERY)

    assert outcome.complete is False
    assert [offer.offer_id for offer in outcome.offers] == ["sku_air"]
    by_supplier = {result.supplier: result for result in outcome.results}
    assert by_supplier["MockAir"].complete is True
    assert by_supplier["MockAir"].cached is False
    assert by_supplier["MockPortal"].complete is False
    failure = by_supplier["MockPortal"].failures[0]
    assert failure.product_type == "flight"
    assert failure.code == "unavailable"
    assert failure.retryable is False
    assert failure.message == "MockPortal is unavailable: access denied"


def test_per_supplier_timeout_does_not_hide_the_other_supplier(db: Session) -> None:
    slow = FakeAdapter(
        "SlowPortal",
        offers=(make_offer(supplier="SlowPortal", offer_id="sku_slow"),),
        delay=0.5,
    )
    quick = FakeAdapter(
        "QuickPortal",
        offers=(make_offer(supplier="QuickPortal", offer_id="sku_quick"),),
    )

    started = time.monotonic()
    outcome = service(slow, quick, timeout=timedelta(milliseconds=50)).search(db, QUERY)
    elapsed = time.monotonic() - started

    assert elapsed < 0.4
    assert outcome.complete is False
    assert [offer.offer_id for offer in outcome.offers] == ["sku_quick"]
    by_supplier = {result.supplier: result for result in outcome.results}
    assert by_supplier["QuickPortal"].complete is True
    failure = by_supplier["SlowPortal"].failures[0]
    assert failure.code == "timeout"
    assert failure.retryable is True

    row = db.execute(select(SupplierSearch)).scalars().one()
    assert row.status == "partial"
    assert row.offer_count == 1
    assert row.finished_at is not None


def test_unexpected_adapter_failure_is_contained_and_not_leaked(db: Session) -> None:
    broken = FakeAdapter("BrokenPortal", error=RuntimeError("secret portal cookie"))

    outcome = service(broken).search(db, QUERY)

    assert outcome.complete is False
    failure = outcome.results[0].failures[0]
    assert failure.code == "unavailable"
    assert "secret portal cookie" not in failure.message
    assert "RuntimeError" in failure.message


def test_disabled_supplier_is_not_called(db: Session) -> None:
    db.add(
        Supplier(
            name="DisabledPortal",
            type="flight",
            adapter="FakeAdapter",
            status="disabled",
        )
    )
    db.flush()
    adapter = FakeAdapter("DisabledPortal", offers=(make_offer(),))

    outcome = service(adapter).search(db, QUERY)

    assert adapter.calls == 0
    assert outcome.complete is False
    assert outcome.results[0].failures[0].code == "unavailable"


def test_no_adapters_is_a_configuration_error() -> None:
    with pytest.raises(SupplierValidationError, match="adapter"):
        service()


# ---------------------------------------------------------------------------
# Circuit breaker
# ---------------------------------------------------------------------------


def test_repeated_failures_open_the_circuit_and_skip_the_supplier(
    db: Session,
) -> None:
    flaky = FakeAdapter(
        "FlakyPortal",
        error=SupplierTimeoutError("FlakyPortal", "search"),
    )
    search = service(flaky, threshold=2)

    search.search(db, QUERY)
    search.search(db, QUERY)
    assert flaky.calls == 2
    assert search.circuit_state("FlakyPortal") == "open"

    outcome = search.search(db, QUERY)
    assert flaky.calls == 2
    assert outcome.complete is False
    assert outcome.results[0].failures[0].message == "FlakyPortal circuit is open"


def test_the_circuit_allows_a_probe_after_the_reset_window(db: Session) -> None:
    clock = ManualClock(NOW)
    flaky = FakeAdapter("FlakyPortal", error=SupplierTimeoutError("FlakyPortal", "search"))
    search = service(
        flaky,
        clock=clock,
        threshold=1,
        reset_after=timedelta(seconds=30),
    )

    search.search(db, QUERY)
    assert search.circuit_state("FlakyPortal") == "open"

    clock.advance(timedelta(seconds=31))
    assert search.circuit_state("FlakyPortal") == "half_open"
    search.search(db, QUERY)
    assert flaky.calls == 2


def test_a_successful_call_closes_the_circuit(db: Session) -> None:
    clock = ManualClock(NOW)
    flaky = FakeAdapter("FlakyPortal", error=SupplierTimeoutError("FlakyPortal", "search"))
    search = service(flaky, clock=clock, threshold=1)

    search.search(db, QUERY)
    assert search.circuit_state("FlakyPortal") == "open"

    flaky.fail_with(None)
    flaky.offer(make_offer(supplier="FlakyPortal", offer_id="sku_flaky"))
    clock.advance(timedelta(minutes=2))
    search.search(db, QUERY)

    assert search.circuit_state("FlakyPortal") == "closed"


# ---------------------------------------------------------------------------
# Snapshot cache and audit
# ---------------------------------------------------------------------------


def test_a_fresh_snapshot_is_reused_instead_of_calling_the_supplier(db: Session) -> None:
    adapter = FakeAdapter("MockAir", offers=(make_offer(offer_id="sku_air"),))
    search = service(adapter)

    first = search.search(db, QUERY)
    second = search.search(db, QUERY)

    assert adapter.calls == 1
    assert first.complete is True
    assert first.results[0].cached is False
    assert second.complete is True
    assert second.results[0].cached is True
    assert [offer.offer_id for offer in second.offers] == ["sku_air"]
    assert db.execute(select(PriceSnapshot)).scalars().all()
    assert len(db.execute(select(PriceSnapshot)).scalars().all()) == 1


def test_cache_can_be_bypassed(db: Session) -> None:
    adapter = FakeAdapter("MockAir", offers=(make_offer(offer_id="sku_air"),))
    search = service(adapter)

    search.search(db, QUERY)
    search.search(db, QUERY, use_cache=False)

    assert adapter.calls == 2


def test_an_expired_snapshot_is_refreshed(db: Session) -> None:
    clock = ManualClock(NOW)
    adapter = FakeAdapter("MockAir", offers=(make_offer(offer_id="sku_air"),))
    search = service(adapter, clock=clock)

    search.search(db, QUERY)
    clock.advance(timedelta(minutes=11))
    search.search(db, QUERY)

    assert adapter.calls == 2


def test_search_writes_snapshots_and_audit_records(db: Session) -> None:
    good = FakeAdapter("MockAir", offers=(make_offer(offer_id="sku_air"),))
    blocked = FakeAdapter(
        "MockPortal",
        error=SupplierUnavailableError("MockPortal", "access denied"),
    )

    service(good, blocked).search(db, QUERY)

    rows = db.execute(select(PriceSnapshot)).scalars().all()
    assert len(rows) == 1
    assert rows[0].request_hash == request_hash(QUERY)
    assert rows[0].search_id is not None
    actions = audit_actions(db)
    assert "supplier.registered" in actions
    assert "supplier.search.started" in actions
    assert "supplier.search.completed" in actions
    assert "price_snapshot.recorded" in actions


# ---------------------------------------------------------------------------
# Concurrency and request identity
# ---------------------------------------------------------------------------


def test_suppliers_are_queried_in_parallel(db: Session) -> None:
    barrier = threading.Barrier(2)
    first = FakeAdapter(
        "MockAir",
        offers=(make_offer(offer_id="sku_air"),),
        barrier=barrier,
    )
    second = FakeAdapter(
        "MockHotels",
        offers=(make_offer(supplier="MockHotels", offer_id="sku_hotel"),),
        barrier=barrier,
    )

    outcome = service(first, second, timeout=timedelta(seconds=5)).search(db, QUERY)

    assert outcome.complete is True
    assert {offer.offer_id for offer in outcome.offers} == {"sku_air", "sku_hotel"}


def test_request_hash_is_stable_and_query_specific() -> None:
    same = SupplierSearchQuery(
        legs=(
            SearchLeg(
                product_type="flight",
                route="Mumbai-Goa",
                travel_date=date(2026, 12, 1),
            ),
        )
    )
    different = SupplierSearchQuery(
        legs=(
            SearchLeg(
                product_type="flight",
                route="Mumbai-Goa",
                travel_date=date(2026, 12, 1),
            ),
        ),
        max_results_per_leg=5,
    )

    assert request_hash(QUERY) == request_hash(same)
    assert request_hash(QUERY) != request_hash(different)
    assert len(request_hash(QUERY)) == 64
    assert int(request_hash(QUERY), 16) >= 0
