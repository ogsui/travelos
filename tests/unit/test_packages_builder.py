"""Deterministic package assembly from normalized offers (task 013a).

No database, no LLM, no network: every expectation here is exact integer paise and
a named explanation reason.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from travelos.inquiry.requirement import Requirement
from travelos.packages.builder import build_package
from travelos.packages.models import ComponentType, ProblemCode, SelectionReason
from travelos.suppliers.models import CancellationRules, FareRules, Offer

NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
START = date(2027, 1, 10)
END = date(2027, 1, 15)  # five nights, six calendar days


def requirement(**overrides: object) -> Requirement:
    values: dict[str, object] = {
        "trip_type": "package",
        "origin_city": "Delhi",
        "destinations": ["Goa"],
        "start_date": START,
        "end_date": END,
        "adults": 2,
        "children_ages": [7],
        "rooms": 2,
        "budget_paise": 50_000_000,
        "preferences": [],
    }
    values.update(overrides)
    return Requirement.model_validate(values)


def offer(
    *,
    supplier: str = "MockAir",
    offer_id: str = "sku_0001",
    product_type: str = "flight",
    name: str | None = None,
    city: str = "Goa",
    route: str | None = "Delhi-Goa",
    currency: str = "INR",
    net_paise: int = 2_000_000,
    taxes_paise: int = 100_000,
    available_count: int = 10,
    fetched_at: datetime | None = None,
    expires_at: datetime | None = None,
) -> Offer:
    return Offer(
        supplier=supplier,
        offer_id=offer_id,
        product_type=product_type,
        name=name or f"{supplier} {product_type}",
        city=city,
        route=route,
        currency=currency,
        net_paise=net_paise,
        taxes_paise=taxes_paise,
        available_count=available_count,
        fare_rules=FareRules(
            cancellation=CancellationRules(
                refundable=True,
                cancellation_days=7,
                change_fee_paise=0,
                currency=currency,
            )
        ),
        fetched_at=fetched_at or NOW,
        expires_at=expires_at or NOW + timedelta(minutes=10),
    )


def base_offers() -> list[Offer]:
    return [
        offer(offer_id="air-out-cheap", net_paise=2_000_000),
        offer(offer_id="air-out-costly", net_paise=2_500_000),
        offer(
            offer_id="air-back",
            route="Goa-Delhi",
            net_paise=2_200_000,
            name="MockAir Goa to Delhi",
        ),
        offer(
            product_type="hotel",
            offer_id="hotel-goa",
            route=None,
            net_paise=500_000,
            taxes_paise=25_000,
            name="Mock Hotels Goa",
        ),
        offer(
            product_type="transfer",
            offer_id="transfer-goa",
            route=None,
            net_paise=100_000,
            taxes_paise=5_000,
            name="Mock Transfers Goa",
        ),
    ]


def test_a_family_package_has_flights_hotel_and_transfer_with_exact_cost() -> None:
    build = build_package(requirement(), base_offers(), now=NOW)

    assert build.ok is True
    itinerary = build.itinerary
    assert itinerary is not None
    assert itinerary.trip_kind == "family"
    assert itinerary.currency == "INR"
    assert [component.component_type for component in itinerary.components] == [
        "flight",
        "transfer",
        "hotel",
        "flight",
    ]

    outbound, transfer, hotel, returning = itinerary.components
    assert outbound.offer.offer_id == "air-out-cheap"
    assert outbound.quantity == 3
    assert outbound.unit_basis == "traveller"
    assert outbound.cost.paise == 6_300_000
    assert outbound.reasons == (SelectionReason.CHEAPEST_AVAILABLE,)
    assert "cheapest of 2 available options" in outbound.explanation
    assert "₹63,000.00" in outbound.explanation

    assert hotel.quantity == 2
    assert hotel.unit_basis == "room"
    assert hotel.cost.paise == 1_050_000
    assert hotel.reasons == (SelectionReason.ONLY_AVAILABLE,)
    assert "the only available option" in hotel.explanation

    assert transfer.quantity == 3
    assert transfer.cost.paise == 315_000
    assert returning.offer.offer_id == "air-back"
    assert returning.cost.paise == 6_900_000

    assert itinerary.total_cost.paise == 14_565_000
    assert itinerary.occupancy.booked_rooms == 2
    assert len(itinerary.days) == 6
    assert itinerary.days[2].components == ()


def test_every_component_is_explained_with_a_machine_readable_reason() -> None:
    itinerary = build_package(requirement(), base_offers(), now=NOW).itinerary
    assert itinerary is not None

    for component in itinerary.components:
        assert component.reasons
        assert component.explanation.startswith(f"Chose {component.offer.supplier}")
        assert component.offer.offer_id in component.explanation


def test_a_multi_city_route_gets_one_flight_and_stay_per_stop() -> None:
    offers = [
        offer(offer_id="del-goa"),
        offer(offer_id="goa-mum", route="Goa-Mumbai", name="MockAir Goa to Mumbai"),
        offer(offer_id="mum-del", route="Mumbai-Delhi", name="MockAir Mumbai to Delhi"),
        offer(
            product_type="hotel",
            offer_id="hotel-goa",
            route=None,
            city="Goa",
            name="Mock Hotels Goa",
        ),
        offer(
            product_type="hotel",
            offer_id="hotel-mumbai",
            route=None,
            city="Mumbai",
            name="Mock Hotels Mumbai",
        ),
        offer(
            product_type="transfer",
            offer_id="transfer-goa",
            route=None,
            city="Goa",
            name="Mock Transfers Goa",
        ),
        offer(
            product_type="transfer",
            offer_id="transfer-mumbai",
            route=None,
            city="Mumbai",
            name="Mock Transfers Mumbai",
        ),
    ]
    parsed = requirement(destinations=["Goa", "Mumbai"], end_date=date(2027, 1, 16), rooms=1)

    build = build_package(parsed, offers, now=NOW)

    assert build.ok is True
    itinerary = build.itinerary
    assert itinerary is not None
    flights = [c for c in itinerary.components if c.component_type == "flight"]
    assert [flight.offer.offer_id for flight in flights] == ["del-goa", "goa-mum", "mum-del"]
    assert [flight.start_date for flight in flights] == [
        START,
        date(2027, 1, 13),
        date(2027, 1, 16),
    ]

    hotels = [c for c in itinerary.components if c.component_type == "hotel"]
    assert [(hotel.offer.offer_id, hotel.start_date, hotel.end_date) for hotel in hotels] == [
        ("hotel-goa", START, date(2027, 1, 13)),
        ("hotel-mumbai", date(2027, 1, 13), date(2027, 1, 16)),
    ]
    assert len(itinerary.days) == 7


def test_a_flight_only_trip_does_not_add_a_hotel_or_transfer() -> None:
    parsed = requirement(
        trip_type="flight",
        end_date=None,
        children_ages=[],
        rooms=None,
    )

    build = build_package(parsed, base_offers(), now=NOW)

    assert build.ok is True
    itinerary = build.itinerary
    assert itinerary is not None
    assert [component.component_type for component in itinerary.components] == ["flight"]
    assert len(itinerary.days) == 1
    assert itinerary.trip_kind == "individual"


def test_an_explicit_component_set_overrides_the_trip_type_default() -> None:
    parsed = requirement()
    flight_only: frozenset[ComponentType] = frozenset({"flight"})

    build = build_package(parsed, base_offers(), now=NOW, include=flight_only)

    assert build.ok is True
    itinerary = build.itinerary
    assert itinerary is not None
    assert [component.component_type for component in itinerary.components] == [
        "flight",
        "flight",
    ]


def test_an_activity_is_included_only_when_the_customer_asked_for_one() -> None:
    activity = offer(
        product_type="activity",
        offer_id="activity-goa",
        route=None,
        net_paise=200_000,
        taxes_paise=10_000,
        name="Mock Snorkelling Goa",
    )
    parsed = requirement(preferences=["sightseeing"])

    build = build_package(parsed, [*base_offers(), activity], now=NOW)

    assert build.ok is True
    itinerary = build.itinerary
    assert itinerary is not None
    chosen = [c for c in itinerary.components if c.component_type == "activity"]
    assert len(chosen) == 1
    assert chosen[0].quantity == 1
    assert chosen[0].unit_basis == "group"
    assert chosen[0].cost.paise == 210_000


def test_a_visa_trip_adds_one_visa_per_traveller() -> None:
    visa = offer(
        supplier="MockVisa",
        product_type="visa",
        offer_id="visa-singapore",
        route=None,
        city="Singapore",
        net_paise=90_000,
        taxes_paise=4_500,
        name="MockVisa Singapore assistance",
    )
    parsed = requirement(
        trip_type="visa",
        destinations=["Singapore"],
        children_ages=[],
        rooms=None,
    )

    build = build_package(parsed, [visa], now=NOW)

    assert build.ok is True
    itinerary = build.itinerary
    assert itinerary is not None
    assert [c.component_type for c in itinerary.components] == ["visa"]
    assert itinerary.components[0].quantity == 2
    assert itinerary.components[0].unit_basis == "traveller"
    assert itinerary.total_cost.paise == 189_000


def test_invalid_occupancy_is_rejected_with_reasons_and_no_itinerary() -> None:
    parsed = requirement(adults=1, children_ages=[3, 5, 7], rooms=1)

    build = build_package(parsed, base_offers(), now=NOW)

    assert build.ok is False
    assert build.itinerary is None
    assert [problem.code for problem in build.problems] == [ProblemCode.TOO_FEW_ADULTS]
    assert "1 adult" in build.problems[0].message


def test_a_package_without_an_end_date_is_rejected() -> None:
    build = build_package(requirement(end_date=None), base_offers(), now=NOW)

    assert build.ok is False
    assert ProblemCode.MISSING_END_DATE in {problem.code for problem in build.problems}


def test_a_multi_city_trip_needs_at_least_one_night_per_stop() -> None:
    parsed = requirement(destinations=["Goa", "Mumbai"], end_date=date(2027, 1, 11), rooms=1)

    build = build_package(parsed, base_offers(), now=NOW)

    assert build.ok is False
    assert ProblemCode.STAY_TOO_SHORT in {problem.code for problem in build.problems}


def test_a_sold_out_hotel_blocks_the_package_with_a_reason() -> None:
    offers = [
        candidate
        for candidate in base_offers()
        if not (candidate.product_type == "hotel" and candidate.offer_id == "hotel-goa")
    ]
    offers.append(
        offer(
            product_type="hotel",
            offer_id="hotel-full",
            route=None,
            available_count=0,
            name="Mock Hotels Goa",
        )
    )

    build = build_package(requirement(), offers, now=NOW)

    assert build.ok is False
    assert ProblemCode.NO_OFFER in {problem.code for problem in build.problems}
    problem = next(p for p in build.problems if p.code is ProblemCode.NO_OFFER)
    assert "hotel" in problem.message
    assert "Goa" in problem.message
    assert "availability" in problem.message


def test_an_expired_offer_is_never_selected() -> None:
    offers = [
        offer(
            offer_id="air-expired",
            net_paise=1_000_000,
            fetched_at=NOW - timedelta(minutes=5),
            expires_at=NOW - timedelta(seconds=1),
        ),
        *[candidate for candidate in base_offers() if candidate.offer_id != "air-out-cheap"],
    ]

    build = build_package(requirement(), offers, now=NOW)

    assert build.ok is True
    itinerary = build.itinerary
    assert itinerary is not None
    outbound = itinerary.components[0]
    assert outbound.offer.offer_id != "air-expired"
    assert "expired" not in outbound.explanation
    assert outbound.offer.offer_id == "air-out-costly"


def test_an_offer_in_another_currency_is_not_mixed_into_an_inr_package() -> None:
    offers = [
        candidate
        for candidate in base_offers()
        if not (candidate.product_type == "hotel" and candidate.offer_id == "hotel-goa")
    ]
    offers.append(
        offer(
            product_type="hotel",
            offer_id="hotel-usd",
            route=None,
            currency="USD",
            net_paise=900_000,
            taxes_paise=45_000,
            name="Mock Hotels Goa USD",
        )
    )

    build = build_package(requirement(), offers, now=NOW)

    assert build.ok is False
    problem = next(p for p in build.problems if p.code is ProblemCode.NO_OFFER)
    assert "another currency" in problem.message


def test_ties_are_broken_deterministically_by_supplier_then_offer_id() -> None:
    offers = [
        offer(supplier="MockAir", offer_id="air-z"),
        offer(supplier="AlphaAir", offer_id="air-a"),
        *[candidate for candidate in base_offers() if candidate.offer_id == "air-back"],
        *[
            candidate
            for candidate in base_offers()
            if candidate.product_type in {"hotel", "transfer"}
        ],
    ]

    build = build_package(requirement(), offers, now=NOW)

    assert build.ok is True
    itinerary = build.itinerary
    assert itinerary is not None
    assert itinerary.components[0].offer.supplier == "AlphaAir"


def test_the_earliest_supplier_deadline_is_exposed() -> None:
    offers = [
        offer(offer_id="air-out-cheap", expires_at=NOW + timedelta(minutes=5)),
        offer(
            offer_id="air-back",
            route="Goa-Delhi",
            expires_at=NOW + timedelta(minutes=30),
        ),
        offer(
            product_type="hotel",
            offer_id="hotel-goa",
            route=None,
            expires_at=NOW + timedelta(minutes=20),
        ),
        offer(
            product_type="transfer",
            offer_id="transfer-goa",
            route=None,
            expires_at=NOW + timedelta(minutes=10),
        ),
    ]

    itinerary = build_package(requirement(), offers, now=NOW).itinerary

    assert itinerary is not None
    assert itinerary.earliest_expiry == NOW + timedelta(minutes=5)
    assert [deadline.offer_id for deadline in itinerary.supplier_deadlines] == [
        "air-out-cheap",
        "transfer-goa",
        "hotel-goa",
        "air-back",
    ]


def test_an_empty_component_request_is_rejected() -> None:
    build = build_package(requirement(), base_offers(), now=NOW, include=frozenset())

    assert build.ok is False
    assert build.problems[0].code is ProblemCode.NO_COMPONENTS
