"""The supplier adapter contract (task 012a1).

Every supplier adapter — the simulator now, an authorized portal adapter once the
owner supplies access — must pass this suite unchanged. Behaviour that only a
simulator can be asked for (injected timeouts, price drift, partial failures,
hold expiry) is tested in ``tests/unit/test_suppliers_simulator.py`` instead.

Register a sandbox target in ``TARGET_FACTORIES`` when one exists. A target must
not need network access at import time; the fixture builds it per test.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest

from travelos.core.clock import ManualClock
from travelos.core.money import Money
from travelos.suppliers.adapter import SupplierAdapter
from travelos.suppliers.errors import (
    BookingNotFoundError,
    HoldNotFoundError,
    IdempotencyConflictError,
    OfferNotFoundError,
    OfferUnavailableError,
)
from travelos.suppliers.models import (
    BookingRequest,
    CancelRequest,
    HoldRequest,
    SearchLeg,
    SearchResult,
    SupplierSearchQuery,
)
from travelos.suppliers.simulator import SupplierSimulator

NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)


@dataclass(frozen=True)
class ContractTarget:
    """One adapter plus the fixture-specific ids the contract needs."""

    name: str
    adapter: SupplierAdapter
    query: SupplierSearchQuery
    unavailable_offer_id: str | None = None


def _price_point(
    *,
    net_paise: int,
    taxes_paise: int,
    available_count: int,
    refundable: bool,
) -> dict[str, object]:
    return {
        "at": "2026-10-01T00:00:00Z",
        "net_paise": net_paise,
        "taxes_paise": taxes_paise,
        "available_count": available_count,
        "fare_rules": {
            "refundable": refundable,
            "change_fee_paise": 0,
            "cancellation_days": 7,
        },
    }


def _simulator_target() -> ContractTarget:
    inventory = [
        {
            "id": "sku_0001",
            "supplier": "MockAir",
            "product_type": "flight",
            "name": "MockAir Mumbai to Goa",
            "route": "Mumbai-Goa",
            "city": "Goa",
            "valid_from": "2026-10-01",
            "valid_until": "2027-03-31",
            "currency": "INR",
            "price_points": [
                _price_point(
                    net_paise=3_000_000,
                    taxes_paise=150_000,
                    available_count=6,
                    refundable=True,
                )
            ],
        },
        {
            "id": "sku_0002",
            "supplier": "MockAir",
            "product_type": "flight",
            "name": "MockAir Mumbai to Goa Sold Out",
            "route": "Mumbai-Goa",
            "city": "Goa",
            "valid_from": "2026-10-01",
            "valid_until": "2027-03-31",
            "currency": "INR",
            "price_points": [
                _price_point(
                    net_paise=3_100_000,
                    taxes_paise=155_000,
                    available_count=0,
                    refundable=False,
                )
            ],
        },
    ]
    adapter = SupplierSimulator(
        supplier="MockAir",
        inventory=inventory,
        scenarios=(),
        clock=ManualClock(NOW),
        wallet_paise=2_500_000,
    )
    return ContractTarget(
        name="simulator",
        adapter=adapter,
        query=SupplierSearchQuery(legs=(SearchLeg(product_type="flight", route="Mumbai-Goa"),)),
        unavailable_offer_id="sku_0002",
    )


# Add {"sandbox": _sandbox_target} only when the owner supplies an authorized
# sandbox account and its terms permit automated access. No target here may touch
# the network during CI.
TARGET_FACTORIES: dict[str, Callable[[], ContractTarget]] = {
    "simulator": _simulator_target,
}


@pytest.fixture(params=sorted(TARGET_FACTORIES))
def target(request: pytest.FixtureRequest) -> ContractTarget:
    return TARGET_FACTORIES[str(request.param)]()


def _search(target: ContractTarget) -> SearchResult:
    return target.adapter.search(target.query)


def test_adapter_implements_the_interface(target: ContractTarget) -> None:
    assert isinstance(target.adapter, SupplierAdapter)
    assert target.adapter.name


def test_search_returns_normalized_available_offers(target: ContractTarget) -> None:
    result = _search(target)

    assert result.complete is True
    assert result.supplier == target.adapter.name
    assert result.offers
    assert len({offer.offer_id for offer in result.offers}) == len(result.offers)

    for offer in result.offers:
        assert offer.supplier == target.adapter.name
        assert offer.is_available is True
        assert offer.total == offer.net + offer.taxes
        assert offer.total == Money(offer.net_paise + offer.taxes_paise)
        assert isinstance(offer.net_paise, int)
        assert isinstance(offer.taxes_paise, int)
        assert offer.expires_at > offer.fetched_at
        assert offer.fetched_at.utcoffset() == timedelta(0)
        assert offer.expires_at.utcoffset() == timedelta(0)


def test_search_is_repeatable_with_the_same_clock(target: ContractTarget) -> None:
    first = _search(target)
    second = _search(target)

    assert [offer.offer_id for offer in first.offers] == [offer.offer_id for offer in second.offers]
    assert [offer.total_paise for offer in first.offers] == [
        offer.total_paise for offer in second.offers
    ]
    assert first.fetched_at == second.fetched_at


def test_price_and_rules_match_the_search_offer(target: ContractTarget) -> None:
    offer = _search(target).offers[0]

    assert target.adapter.price(offer.offer_id) == offer
    assert target.adapter.rules(offer.offer_id) == offer.fare_rules


def test_hold_then_book_then_retrieve(target: ContractTarget) -> None:
    offer = _search(target).offers[0]
    hold = target.adapter.hold(
        HoldRequest(
            offer_id=offer.offer_id,
            traveller_count=2,
            idempotency_key="contract-hold-0001",
        )
    )

    assert hold.supplier == target.adapter.name
    assert hold.offer == offer
    assert hold.traveller_count == 2
    assert hold.expires_at is not None
    assert hold.expires_at > hold.created_at
    assert hold.is_expired(NOW) is False

    booked = target.adapter.book(
        BookingRequest(hold_id=hold.hold_id, idempotency_key="contract-book-0001")
    )
    assert booked.state == "confirmed"
    assert booked.supplier == target.adapter.name
    assert booked.offer_id == offer.offer_id
    assert booked.traveller_count == 2
    assert booked.total == offer.total
    assert booked.booked_at.utcoffset() == timedelta(0)
    assert target.adapter.retrieve(booked.booking_ref) == booked


def test_hold_is_idempotent(target: ContractTarget) -> None:
    offer = _search(target).offers[0]
    request = HoldRequest(
        offer_id=offer.offer_id,
        traveller_count=1,
        idempotency_key="contract-hold-0001",
    )

    assert target.adapter.hold(request).hold_id == target.adapter.hold(request).hold_id


def test_a_reused_idempotency_key_for_a_different_request_is_refused(
    target: ContractTarget,
) -> None:
    offer = _search(target).offers[0]
    target.adapter.hold(
        HoldRequest(
            offer_id=offer.offer_id,
            traveller_count=1,
            idempotency_key="contract-hold-0001",
        )
    )

    with pytest.raises(IdempotencyConflictError):
        target.adapter.hold(
            HoldRequest(
                offer_id=offer.offer_id,
                traveller_count=2,
                idempotency_key="contract-hold-0001",
            )
        )


def test_booking_is_idempotent(target: ContractTarget) -> None:
    offer = _search(target).offers[0]
    hold = target.adapter.hold(
        HoldRequest(
            offer_id=offer.offer_id,
            traveller_count=1,
            idempotency_key="contract-hold-0001",
        )
    )
    request = BookingRequest(hold_id=hold.hold_id, idempotency_key="contract-book-0001")

    first = target.adapter.book(request)
    second = target.adapter.book(request)

    assert first.booking_ref == second.booking_ref
    assert first == second


def test_booking_without_a_hold_is_typed(target: ContractTarget) -> None:
    with pytest.raises(HoldNotFoundError):
        target.adapter.book(
            BookingRequest(hold_id="hold_missing", idempotency_key="contract-book-0001")
        )


def test_cancel_is_idempotent_and_retrieve_shows_the_state(
    target: ContractTarget,
) -> None:
    offer = _search(target).offers[0]
    hold = target.adapter.hold(
        HoldRequest(
            offer_id=offer.offer_id,
            traveller_count=1,
            idempotency_key="contract-hold-0001",
        )
    )
    booked = target.adapter.book(
        BookingRequest(hold_id=hold.hold_id, idempotency_key="contract-book-0001")
    )

    first = target.adapter.cancel(
        CancelRequest(booking_ref=booked.booking_ref, idempotency_key="contract-cancel-0001")
    )
    second = target.adapter.cancel(
        CancelRequest(booking_ref=booked.booking_ref, idempotency_key="contract-cancel-0002")
    )

    assert first == second
    assert first.state == "cancelled"
    assert first.refund + first.fee == booked.total
    assert target.adapter.retrieve(booked.booking_ref).state == "cancelled"


def test_unknown_offers_are_typed(target: ContractTarget) -> None:
    for operation in (target.adapter.price, target.adapter.rules):
        with pytest.raises(OfferNotFoundError):
            operation("sku_missing")

    with pytest.raises(OfferNotFoundError):
        target.adapter.hold(
            HoldRequest(
                offer_id="sku_missing",
                traveller_count=1,
                idempotency_key="contract-hold-0001",
            )
        )


def test_unknown_bookings_are_typed(target: ContractTarget) -> None:
    with pytest.raises(BookingNotFoundError):
        target.adapter.retrieve("bkg_missing")
    with pytest.raises(BookingNotFoundError):
        target.adapter.cancel(
            CancelRequest(booking_ref="bkg_missing", idempotency_key="contract-cancel-0001")
        )


def test_unavailable_offers_are_typed(target: ContractTarget) -> None:
    if target.unavailable_offer_id is None:
        pytest.skip("this adapter has no known sold-out fixture offer")

    with pytest.raises(OfferUnavailableError):
        target.adapter.price(target.unavailable_offer_id)


def test_wallet_read_is_typed_and_stable(target: ContractTarget) -> None:
    first = target.adapter.wallet_balance()
    second = target.adapter.wallet_balance()

    assert first == second
    assert first.supplier == target.adapter.name
    assert first.balance_paise >= 0
    assert first.balance == Money(first.balance_paise, first.currency)
    assert first.as_of.utcoffset() == timedelta(0)


def test_booking_and_cancel_never_move_money_in_the_wallet(
    target: ContractTarget,
) -> None:
    """The system records supplier balances; only humans move supplier money."""

    before = target.adapter.wallet_balance()
    offer = _search(target).offers[0]
    hold = target.adapter.hold(
        HoldRequest(
            offer_id=offer.offer_id,
            traveller_count=1,
            idempotency_key="contract-hold-0001",
        )
    )
    booked = target.adapter.book(
        BookingRequest(hold_id=hold.hold_id, idempotency_key="contract-book-0001")
    )
    target.adapter.cancel(
        CancelRequest(booking_ref=booked.booking_ref, idempotency_key="contract-cancel-0001")
    )

    assert target.adapter.wallet_balance() == before
