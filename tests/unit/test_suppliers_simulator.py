"""SupplierSimulator: deterministic drift, failures, holds and bookings (task 012a1).

The simulator reads the same synthetic shapes as ``data/synthetic`` and is the
reference implementation the supplier contract suite runs against. No test here
touches a database, network or wall clock.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from datetime import UTC, date, datetime, timedelta

import pytest

from travelos.core.clock import ManualClock
from travelos.core.money import Money
from travelos.suppliers.errors import (
    BookingNotFoundError,
    BookingStateError,
    HoldExpiredError,
    HoldNotFoundError,
    IdempotencyConflictError,
    OfferNotFoundError,
    OfferUnavailableError,
    SupplierSimulatorError,
    SupplierTimeoutError,
)
from travelos.suppliers.models import (
    BookingRequest,
    CancelRequest,
    FailureCode,
    HoldRequest,
    SearchLeg,
    SupplierSearchQuery,
)
from travelos.suppliers.simulator import SupplierSimulator

NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
TRAVEL_DATE = date(2026, 12, 1)
SCENARIO_AT = "2026-11-01T09:00:00Z"


def price_point(
    *,
    at: str = "2026-10-01T00:00:00Z",
    net_paise: int = 3_000_000,
    taxes_paise: int = 150_000,
    available_count: int = 10,
    refundable: bool = True,
    change_fee_paise: int = 0,
    cancellation_days: int = 7,
) -> dict[str, object]:
    return {
        "at": at,
        "net_paise": net_paise,
        "taxes_paise": taxes_paise,
        "available_count": available_count,
        "fare_rules": {
            "refundable": refundable,
            "change_fee_paise": change_fee_paise,
            "cancellation_days": cancellation_days,
        },
    }


def inventory_record(
    *,
    offer_id: str,
    product_type: str,
    name: str,
    route: str | None,
    city: str,
    price_points: Iterable[Mapping[str, object]],
    supplier: str = "MockAir",
    currency: str = "INR",
    valid_from: str = "2026-10-01",
    valid_until: str = "2027-03-31",
) -> dict[str, object]:
    return {
        "id": offer_id,
        "supplier": supplier,
        "product_type": product_type,
        "name": name,
        "route": route,
        "city": city,
        "valid_from": valid_from,
        "valid_until": valid_until,
        "currency": currency,
        "price_points": [dict(point) for point in price_points],
    }


def inventory() -> list[dict[str, object]]:
    return [
        inventory_record(
            offer_id="sku_0001",
            product_type="flight",
            name="MockAir Mumbai to Goa",
            route="Mumbai-Goa",
            city="Goa",
            price_points=[
                price_point(),
                price_point(
                    at="2026-10-15T00:00:00Z",
                    net_paise=3_100_000,
                    taxes_paise=155_000,
                    available_count=8,
                    refundable=False,
                    change_fee_paise=250_000,
                    cancellation_days=10,
                ),
            ],
        ),
        inventory_record(
            offer_id="sku_0002",
            product_type="flight",
            name="MockAir Delhi to Jaipur",
            route="Delhi-Jaipur",
            city="Jaipur",
            price_points=[
                price_point(
                    net_paise=4_000_000,
                    taxes_paise=200_000,
                    available_count=5,
                )
            ],
        ),
        inventory_record(
            offer_id="sku_0003",
            product_type="hotel",
            name="Mock Goa Beach Hotel",
            route=None,
            city="Goa",
            price_points=[price_point(net_paise=500_000, taxes_paise=25_000, available_count=4)],
        ),
        inventory_record(
            offer_id="sku_0005",
            product_type="flight",
            name="MockAir Chennai to Goa",
            route="Chennai-Goa",
            city="Goa",
            price_points=[
                price_point(
                    at="2026-12-01T00:00:00Z",
                    net_paise=2_000_000,
                    taxes_paise=100_000,
                    available_count=5,
                )
            ],
        ),
        inventory_record(
            offer_id="sku_0006",
            product_type="flight",
            name="MockAir Kolkata to Goa",
            route="Kolkata-Goa",
            city="Goa",
            price_points=[price_point(net_paise=2_500_000, taxes_paise=125_000, available_count=5)],
        ),
        inventory_record(
            offer_id="sku_0007",
            product_type="flight",
            name="MockAir Mumbai to Dubai",
            route="Mumbai-Dubai",
            city="Dubai",
            price_points=[price_point(net_paise=8_000_000, taxes_paise=400_000, available_count=5)],
        ),
        inventory_record(
            offer_id="sku_0008",
            product_type="flight",
            name="MockAir Mumbai to Goa Express",
            route="Mumbai-Goa",
            city="Goa",
            price_points=[price_point(net_paise=3_200_000, taxes_paise=160_000, available_count=5)],
        ),
    ]


def scenarios() -> list[dict[str, object]]:
    return [
        {
            "id": "scn_0001",
            "sku_id": "sku_0002",
            "scenario": "price_change",
            "at": SCENARIO_AT,
            "details": {
                "old_net_paise": 4_000_000,
                "new_net_paise": 4_500_000,
                "change_pct": 12,
            },
        },
        {
            "id": "scn_0002",
            "sku_id": "sku_0006",
            "scenario": "sold_out",
            "at": SCENARIO_AT,
            "details": {"available_count": 0},
        },
        {
            "id": "scn_0003",
            "sku_id": "sku_0007",
            "scenario": "timeout",
            "at": SCENARIO_AT,
            "details": {"after_ms": 5_000, "retryable": True},
        },
        {
            "id": "scn_0004",
            "sku_id": "sku_0003",
            "scenario": "partial_failure",
            "at": SCENARIO_AT,
            "details": {"succeeded_legs": ["flight"], "failed_legs": ["hotel"]},
        },
        {
            "id": "scn_0005",
            "sku_id": "sku_0001",
            "scenario": "hold_expiry",
            "at": SCENARIO_AT,
            "details": {"hold_seconds": 120, "expires": True},
        },
    ]


def simulator(
    *,
    clock: ManualClock | None = None,
    rows: Iterable[Mapping[str, object]] | None = None,
    scenario_rows: Iterable[Mapping[str, object]] | None = None,
    wallet_paise: int = 5_000_000,
    offer_ttl: timedelta = timedelta(minutes=10),
    hold_seconds: int = 900,
) -> SupplierSimulator:
    return SupplierSimulator(
        supplier="MockAir",
        inventory=inventory() if rows is None else rows,
        scenarios=scenarios() if scenario_rows is None else scenario_rows,
        clock=clock or ManualClock(NOW),
        wallet_paise=wallet_paise,
        offer_ttl=offer_ttl,
        hold_seconds=hold_seconds,
    )


def flight_query(*, max_results_per_leg: int = 10, **overrides: object) -> SupplierSearchQuery:
    values: dict[str, object] = {
        "product_type": "flight",
        "route": "Mumbai-Goa",
        "travel_date": TRAVEL_DATE,
    }
    values.update(overrides)
    return SupplierSearchQuery(
        legs=(SearchLeg.model_validate(values),),
        max_results_per_leg=max_results_per_leg,
    )


# ---------------------------------------------------------------------------
# Search
# ---------------------------------------------------------------------------


def test_search_returns_normalized_current_offers() -> None:
    result = simulator().search(flight_query())

    assert result.complete is True
    assert result.failures == ()
    assert result.fetched_at == NOW
    by_id = {offer.offer_id: offer for offer in result.offers}
    assert set(by_id) == {"sku_0001", "sku_0008"}

    offer = by_id["sku_0001"]
    assert offer.supplier == "MockAir"
    assert offer.product_type == "flight"
    assert offer.net == Money(3_100_000)
    assert offer.taxes == Money(155_000)
    assert offer.total == Money(3_255_000)
    assert offer.fare_rules.refundable is False
    assert offer.fare_rules.cancellation.cancellation_days == 10
    assert offer.fetched_at == NOW
    assert offer.expires_at == NOW + timedelta(minutes=10)


def test_search_sorts_by_total_and_honours_max_results() -> None:
    simulator_ = simulator()
    all_offers = simulator_.search(flight_query()).offers
    assert [offer.offer_id for offer in all_offers] == ["sku_0001", "sku_0008"]

    query = flight_query(max_results_per_leg=1)
    limited = simulator_.search(query)
    assert [offer.offer_id for offer in limited.offers] == ["sku_0001"]


def test_search_matching_is_case_insensitive_and_date_bounded() -> None:
    simulator_ = simulator()

    assert simulator_.search(flight_query(route="mumbai-goa")).offers
    assert simulator_.search(flight_query(travel_date=date(2027, 5, 1))).offers == ()

    no_match = simulator_.search(flight_query(route="Nowhere-Goa"))
    assert no_match.offers == ()
    assert no_match.complete is True


def test_search_hides_sold_out_and_not_yet_on_sale_offers() -> None:
    result = simulator().search(flight_query(city="Goa", route=None))

    ids = {offer.offer_id for offer in result.offers}
    assert "sku_0006" not in ids  # sold_out scenario
    assert "sku_0005" not in ids  # first price point is in the future


def test_search_reports_partial_results_when_one_leg_fails() -> None:
    query = SupplierSearchQuery(
        legs=(
            SearchLeg(product_type="flight", route="Mumbai-Goa", travel_date=TRAVEL_DATE),
            SearchLeg(product_type="hotel", city="Goa", travel_date=TRAVEL_DATE),
        )
    )
    result = simulator().search(query)

    assert {offer.offer_id for offer in result.offers} == {"sku_0001", "sku_0008"}
    assert result.complete is False
    assert len(result.failures) == 1
    failure = result.failures[0]
    assert failure.product_type == "hotel"
    assert failure.code == FailureCode.PARTIAL_FAILURE
    assert failure.retryable is False


def test_search_reports_a_timeout_for_a_matching_leg() -> None:
    result = simulator().search(flight_query(route="Mumbai-Dubai", city=None))

    assert result.offers == ()
    assert result.complete is False
    assert [(failure.code, failure.retryable) for failure in result.failures] == [
        (FailureCode.TIMEOUT, True)
    ]


def test_search_before_any_price_point_returns_nothing() -> None:
    clock = ManualClock(datetime(2026, 9, 15, tzinfo=UTC))
    result = simulator(clock=clock).search(flight_query())

    assert result.offers == ()
    assert result.failures == ()


# ---------------------------------------------------------------------------
# Price and rules
# ---------------------------------------------------------------------------


def test_price_uses_the_latest_price_point_at_or_before_now() -> None:
    clock = ManualClock(datetime(2026, 10, 10, tzinfo=UTC))
    simulator_ = simulator(clock=clock)
    assert simulator_.price("sku_0001").net == Money(3_000_000)

    clock.set(datetime(2026, 10, 20, tzinfo=UTC))
    assert simulator_.price("sku_0001").net == Money(3_100_000)


def test_price_before_any_point_is_unavailable() -> None:
    clock = ManualClock(datetime(2026, 9, 15, tzinfo=UTC))
    with pytest.raises(OfferUnavailableError, match="not on sale"):
        simulator(clock=clock).price("sku_0001")


def test_unknown_offer_is_typed() -> None:
    simulator_ = simulator()
    with pytest.raises(OfferNotFoundError):
        simulator_.price("sku_9999")
    with pytest.raises(OfferNotFoundError):
        simulator_.rules("sku_9999")


def test_price_change_scenario_applies_from_its_timestamp() -> None:
    clock = ManualClock(datetime(2026, 11, 1, 8, 0, tzinfo=UTC))
    simulator_ = simulator(clock=clock)
    assert simulator_.price("sku_0002").net == Money(4_000_000)

    clock.set(datetime(2026, 11, 1, 10, 0, tzinfo=UTC))
    changed = simulator_.price("sku_0002")
    assert changed.net == Money(4_500_000)
    assert changed.taxes == Money(200_000)  # a price change carries no tax rule


def test_sold_out_scenario_blocks_price_but_not_rules() -> None:
    simulator_ = simulator()
    with pytest.raises(OfferUnavailableError):
        simulator_.price("sku_0006")

    rules = simulator_.rules("sku_0006")
    assert rules.refundable is True
    assert rules.change_fee == Money(0)


def test_timeout_scenario_is_typed_and_immediate() -> None:
    simulator_ = simulator()
    with pytest.raises(SupplierTimeoutError, match="sku_0007"):
        simulator_.price("sku_0007")
    with pytest.raises(SupplierTimeoutError):
        simulator_.rules("sku_0007")


# ---------------------------------------------------------------------------
# Holds and bookings
# ---------------------------------------------------------------------------


def test_hold_uses_the_scenario_expiry_and_is_idempotent() -> None:
    simulator_ = simulator()
    request = HoldRequest(
        offer_id="sku_0001",
        traveller_count=2,
        idempotency_key="hold-key-0001",
    )

    first = simulator_.hold(request)
    second = simulator_.hold(request)

    assert first.hold_id == second.hold_id
    assert first.created_at == NOW
    assert first.expires_at == NOW + timedelta(seconds=120)
    assert first.traveller_count == 2
    assert first.offer.net == Money(3_100_000)
    assert first.is_expired(NOW + timedelta(seconds=119)) is False
    assert first.is_expired(NOW + timedelta(seconds=120)) is True


def test_reusing_an_idempotency_key_for_a_different_request_conflicts() -> None:
    simulator_ = simulator()
    simulator_.hold(
        HoldRequest(offer_id="sku_0001", traveller_count=2, idempotency_key="hold-key-0001")
    )

    with pytest.raises(IdempotencyConflictError):
        simulator_.hold(
            HoldRequest(
                offer_id="sku_0001",
                traveller_count=3,
                idempotency_key="hold-key-0001",
            )
        )


def test_hold_checks_availability_for_the_traveller_count() -> None:
    simulator_ = simulator()
    request = HoldRequest(
        offer_id="sku_0001",
        traveller_count=9,  # only 8 seats at the current price point
        idempotency_key="hold-key-0001",
    )

    with pytest.raises(OfferUnavailableError, match="travellers"):
        simulator_.hold(request)


def test_hold_of_a_timed_out_offer_is_typed() -> None:
    simulator_ = simulator()
    with pytest.raises(SupplierTimeoutError):
        simulator_.hold(
            HoldRequest(
                offer_id="sku_0007",
                traveller_count=1,
                idempotency_key="hold-key-0007",
            )
        )


def test_book_requires_a_live_hold_and_is_idempotent() -> None:
    simulator_ = simulator()
    hold = simulator_.hold(
        HoldRequest(offer_id="sku_0001", traveller_count=2, idempotency_key="hold-key-0001")
    )
    request = BookingRequest(hold_id=hold.hold_id, idempotency_key="book-key-0001")

    first = simulator_.book(request)
    second = simulator_.book(request)

    assert first.booking_ref == second.booking_ref
    assert first.state == "confirmed"
    assert first.is_active is True
    assert first.supplier == "MockAir"
    assert first.offer_id == "sku_0001"
    assert first.traveller_count == 2
    assert first.total == Money(3_255_000)
    assert first.booked_at == NOW


def test_booking_with_an_unknown_hold_is_typed() -> None:
    with pytest.raises(HoldNotFoundError):
        simulator().book(BookingRequest(hold_id="hold_missing", idempotency_key="book-key-0001"))


def test_booking_a_hold_twice_with_a_new_key_is_refused() -> None:
    simulator_ = simulator()
    hold = simulator_.hold(
        HoldRequest(offer_id="sku_0001", traveller_count=1, idempotency_key="hold-key-0001")
    )
    simulator_.book(BookingRequest(hold_id=hold.hold_id, idempotency_key="book-key-0001"))

    with pytest.raises(BookingStateError, match="already booked"):
        simulator_.book(BookingRequest(hold_id=hold.hold_id, idempotency_key="book-key-0002"))


def test_expired_holds_cannot_be_booked() -> None:
    clock = ManualClock(NOW)
    simulator_ = simulator(clock=clock)
    hold = simulator_.hold(
        HoldRequest(offer_id="sku_0001", traveller_count=1, idempotency_key="hold-key-0001")
    )
    clock.advance(timedelta(seconds=120))

    with pytest.raises(HoldExpiredError):
        simulator_.book(BookingRequest(hold_id=hold.hold_id, idempotency_key="book-key-0001"))


def test_retrieve_returns_the_booking_or_a_typed_error() -> None:
    simulator_ = simulator()
    hold = simulator_.hold(
        HoldRequest(offer_id="sku_0001", traveller_count=1, idempotency_key="hold-key-0001")
    )
    booked = simulator_.book(BookingRequest(hold_id=hold.hold_id, idempotency_key="book-key-0001"))

    assert simulator_.retrieve(booked.booking_ref) == booked
    with pytest.raises(BookingNotFoundError):
        simulator_.retrieve("bkg_missing")


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------


def test_cancellation_refunds_a_refundable_fare_and_is_idempotent() -> None:
    simulator_ = simulator()
    hold = simulator_.hold(
        HoldRequest(offer_id="sku_0002", traveller_count=2, idempotency_key="hold-key-0002")
    )
    booked = simulator_.book(BookingRequest(hold_id=hold.hold_id, idempotency_key="book-key-0002"))

    first = simulator_.cancel(
        CancelRequest(booking_ref=booked.booking_ref, idempotency_key="cancel-key-0001")
    )
    second = simulator_.cancel(
        CancelRequest(booking_ref=booked.booking_ref, idempotency_key="cancel-key-0002")
    )

    assert first.refund == Money(4_700_000)
    assert first.fee == Money(0)
    assert first.cancelled_at == NOW
    assert second == first
    assert simulator_.retrieve(booked.booking_ref).state == "cancelled"


def test_cancellation_of_a_non_refundable_fare_keeps_the_money() -> None:
    simulator_ = simulator()
    hold = simulator_.hold(
        HoldRequest(offer_id="sku_0001", traveller_count=1, idempotency_key="hold-key-0001")
    )
    booked = simulator_.book(BookingRequest(hold_id=hold.hold_id, idempotency_key="book-key-0001"))

    result = simulator_.cancel(
        CancelRequest(booking_ref=booked.booking_ref, idempotency_key="cancel-key-0001")
    )

    assert result.refund == Money(0)
    assert result.fee == Money(3_255_000)


def test_cancelling_an_unknown_booking_is_typed() -> None:
    with pytest.raises(BookingNotFoundError):
        simulator().cancel(
            CancelRequest(booking_ref="bkg_missing", idempotency_key="cancel-key-0001")
        )


# ---------------------------------------------------------------------------
# Wallet
# ---------------------------------------------------------------------------


def test_wallet_read_is_read_only_and_uses_the_clock() -> None:
    simulator_ = simulator(wallet_paise=5_000_000)

    first = simulator_.wallet_balance()
    second = simulator_.wallet_balance()

    assert first == second
    assert first.supplier == "MockAir"
    assert first.balance == Money(5_000_000)
    assert first.as_of == NOW


# ---------------------------------------------------------------------------
# Fixture loading and validation
# ---------------------------------------------------------------------------


def test_from_dataset_loads_the_synthetic_supplier_and_scenarios() -> None:
    clock = ManualClock(datetime(2026, 9, 20, tzinfo=UTC))
    simulator_ = SupplierSimulator.from_dataset("MockAir", clock=clock)

    offers = simulator_.search(
        SupplierSearchQuery(
            legs=(
                SearchLeg(
                    product_type="flight",
                    city="Goa",
                    travel_date=TRAVEL_DATE,
                ),
            )
        )
    )
    assert offers.offers
    assert all(offer.supplier == "MockAir" for offer in offers.offers)

    before = simulator_.price("sku_0006")
    clock.set(datetime(2026, 9, 28, tzinfo=UTC))
    after = simulator_.price("sku_0006")
    assert before.net != Money(4_950_000)
    assert after.net == Money(4_950_000)


def test_unknown_supplier_is_reported() -> None:
    with pytest.raises(SupplierSimulatorError, match="No supplier"):
        SupplierSimulator.from_dataset("NotASupplier")


def test_rows_for_another_supplier_are_rejected() -> None:
    rows = inventory()
    rows[0]["supplier"] = "MockHotels"

    with pytest.raises(SupplierSimulatorError, match="MockHotels"):
        simulator(rows=rows, scenario_rows=[])


def test_duplicate_offer_ids_are_rejected() -> None:
    rows = inventory()
    rows.append(dict(rows[0]))

    with pytest.raises(SupplierSimulatorError, match="duplicate"):
        simulator(rows=rows, scenario_rows=[])


def test_empty_inventory_is_rejected() -> None:
    with pytest.raises(SupplierSimulatorError, match="inventory"):
        simulator(rows=[], scenario_rows=[])


@pytest.mark.parametrize(
    ("field", "value", "message"),
    [
        ("supplier", "", "supplier"),
        ("offer_ttl", timedelta(0), "offer_ttl"),
        ("hold_seconds", 0, "hold_seconds"),
        ("wallet_paise", -1, "wallet_paise"),
    ],
)
def test_simulator_configuration_is_validated(field: str, value: object, message: str) -> None:
    arguments: dict[str, object] = {
        "supplier": "MockAir",
        "inventory": inventory(),
        "scenarios": [],
        "clock": ManualClock(NOW),
        "wallet_paise": 0,
        "offer_ttl": timedelta(minutes=10),
        "hold_seconds": 900,
    }
    arguments[field] = value

    with pytest.raises(SupplierSimulatorError, match=message):
        SupplierSimulator(**arguments)  # type: ignore[arg-type]


def test_invalid_inventory_shapes_are_rejected() -> None:
    rows = inventory()
    rows[0]["price_points"] = []

    with pytest.raises(SupplierSimulatorError, match="price_points"):
        simulator(rows=rows, scenario_rows=[])

    rows = inventory()
    rows[0]["currency"] = "inr"
    with pytest.raises(SupplierSimulatorError, match="currency"):
        simulator(rows=rows, scenario_rows=[])

    rows = inventory()
    rows[0]["price_points"] = [price_point(at="2026-10-01T00:00:00")]
    with pytest.raises(SupplierSimulatorError, match="timezone"):
        simulator(rows=rows, scenario_rows=[])


def test_scenarios_must_reference_known_offers_and_have_valid_details() -> None:
    unknown = scenarios()
    unknown[0]["sku_id"] = "sku_9999"
    with pytest.raises(SupplierSimulatorError, match="sku_9999"):
        simulator(scenario_rows=unknown)

    bad_details = scenarios()
    bad_details[0]["details"] = {"change_pct": 10}
    with pytest.raises(SupplierSimulatorError, match="new_net_paise"):
        simulator(scenario_rows=bad_details)

    unknown_type = scenarios()
    unknown_type[0]["scenario"] = "meteor_strike"
    with pytest.raises(SupplierSimulatorError, match="scenario"):
        simulator(scenario_rows=unknown_type)


def test_partial_failure_needs_at_least_one_failed_leg() -> None:
    rows = scenarios()
    rows[3]["details"] = {"succeeded_legs": ["flight"], "failed_legs": []}

    with pytest.raises(SupplierSimulatorError, match="failed_legs"):
        simulator(scenario_rows=rows)
