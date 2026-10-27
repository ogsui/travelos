"""Deterministic itinerary assembly from normalized supplier offers (task 013a).

The builder answers one question: given a parsed requirement and the offers that
a supplier search returned, which components form a valid trip and why? It does
not price, tax, book or write to the database. Selection is by lowest total
supplier cost among eligible offers, with a stable tie-break; every chosen
component carries a machine-readable reason and a templated explanation.

Inclusion rules (all documented as agency defaults):
* a flight trip includes flights only;
* a hotel trip includes a hotel and arrival transfers;
* a visa trip includes visas only;
* any other trip includes flights, hotels and arrival transfers;
* an activity is added only when a preference names one;
* a visa is added when a preference names one.

A round trip is assumed whenever ``end_date`` is present. Multi-city destinations
are visited in the order the customer listed them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from travelos.core.money import Money
from travelos.inquiry.requirement import Requirement
from travelos.packages.models import (
    BuildProblem,
    ComponentType,
    Itinerary,
    ItineraryComponent,
    ItineraryDay,
    OccupancyAssessment,
    PackageBuild,
    ProblemCode,
    SelectionReason,
    TravellerParty,
    UnitBasis,
)
from travelos.packages.occupancy import (
    DEFAULT_OCCUPANCY_POLICY,
    OccupancyPolicy,
    assess_occupancy,
)
from travelos.suppliers.models import Offer

_ACTIVITY_TAGS = frozenset(
    {"activity", "activities", "sightseeing", "tour", "tours", "excursion", "excursions"}
)
_TYPE_ORDER: dict[ComponentType, int] = {
    ComponentType.FLIGHT: 0,
    ComponentType.TRANSFER: 1,
    ComponentType.HOTEL: 2,
    ComponentType.ACTIVITY: 3,
    ComponentType.VISA: 4,
}


@dataclass(frozen=True)
class _Stay:
    city: str
    check_in: date
    check_out: date
    nights: int


@dataclass(frozen=True)
class _Selection:
    offer: Offer | None
    quantity: int
    reason: SelectionReason | None
    considered: int
    eligible: int
    expired: int
    unavailable: int
    wrong_currency: int


def build_package(
    requirement: Requirement,
    offers: Iterable[Offer],
    *,
    now: datetime,
    policy: OccupancyPolicy = DEFAULT_OCCUPANCY_POLICY,
    include: frozenset[ComponentType] | None = None,
) -> PackageBuild:
    """Assemble one itinerary, or explain every reason it is impossible."""

    if now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("now must be timezone-aware")
    now = now.astimezone(UTC)
    supplied = tuple(offers)
    components = _requested_components(requirement) if include is None else include

    if not components:
        return PackageBuild(
            problems=(
                BuildProblem(
                    code=ProblemCode.NO_COMPONENTS,
                    message="no itinerary components were requested",
                ),
            )
        )

    party = TravellerParty.from_requirement(requirement)
    needs_rooms = ComponentType.HOTEL in components
    occupancy = assess_occupancy(
        party,
        requirement.rooms if needs_rooms else None,
        policy,
    )
    problems: list[BuildProblem] = [
        BuildProblem(code=problem.code, message=problem.message) for problem in occupancy.problems
    ]
    problems.extend(_structural_problems(requirement, components))
    if problems:
        return PackageBuild(problems=tuple(problems))

    assembly_problems, itinerary = _assemble(
        requirement,
        supplied,
        components=components,
        party=party,
        occupancy=occupancy,
        now=now,
    )
    if assembly_problems:
        return PackageBuild(problems=tuple(assembly_problems))
    if itinerary is None:  # pragma: no cover - guarded by the assembly contract
        raise AssertionError("assembly returned neither an itinerary nor problems")
    return PackageBuild(itinerary=itinerary)


def _requested_components(requirement: Requirement) -> frozenset[ComponentType]:
    trip_type = requirement.trip_type
    if trip_type == "visa":
        components = {ComponentType.VISA}
    elif trip_type == "hotel":
        components = {ComponentType.HOTEL, ComponentType.TRANSFER}
    elif trip_type == "flight":
        components = {ComponentType.FLIGHT}
    else:
        components = {ComponentType.FLIGHT, ComponentType.HOTEL, ComponentType.TRANSFER}

    tags = {_normalise(tag) for tag in requirement.preferences}
    if tags & _ACTIVITY_TAGS:
        components.add(ComponentType.ACTIVITY)
    if any("visa" in tag for tag in tags):
        components.add(ComponentType.VISA)
    return frozenset(components)


def _structural_problems(
    requirement: Requirement,
    components: frozenset[ComponentType],
) -> list[BuildProblem]:
    problems: list[BuildProblem] = []
    if not requirement.destinations:
        problems.append(
            BuildProblem(
                code=ProblemCode.MISSING_DESTINATION,
                message="an itinerary needs at least one destination",
            )
        )
    if ComponentType.FLIGHT in components and not requirement.origin_city:
        problems.append(
            BuildProblem(
                code=ProblemCode.MISSING_ORIGIN,
                message="a flight itinerary needs an origin city",
            )
        )
    if requirement.start_date is None:
        problems.append(
            BuildProblem(
                code=ProblemCode.MISSING_START_DATE,
                message="an itinerary needs a start date",
            )
        )
    needs_end = bool(
        components & {ComponentType.HOTEL, ComponentType.TRANSFER, ComponentType.ACTIVITY}
    )
    if needs_end and requirement.end_date is None:
        problems.append(
            BuildProblem(
                code=ProblemCode.MISSING_END_DATE,
                message="a hotel, transfer or activity needs an end date",
            )
        )
    elif len(requirement.destinations) > 1 and requirement.end_date is None:
        problems.append(
            BuildProblem(
                code=ProblemCode.MISSING_END_DATE,
                message="a multi-city itinerary needs an end date to schedule each stop",
            )
        )
    if (
        requirement.start_date is not None
        and requirement.end_date is not None
        and requirement.end_date <= requirement.start_date
    ):
        problems.append(
            BuildProblem(
                code=ProblemCode.INVALID_DATE_RANGE,
                message="end date must be after the start date",
            )
        )
    elif (
        requirement.start_date is not None
        and requirement.end_date is not None
        and len(requirement.destinations) > 1
        and (requirement.end_date - requirement.start_date).days < len(requirement.destinations)
    ):
        problems.append(
            BuildProblem(
                code=ProblemCode.STAY_TOO_SHORT,
                message=(
                    "a multi-city trip needs at least one night per destination; "
                    f"{len(requirement.destinations)} destinations need "
                    f"{len(requirement.destinations)} nights"
                ),
            )
        )
    return problems


def _assemble(
    requirement: Requirement,
    offers: tuple[Offer, ...],
    *,
    components: frozenset[ComponentType],
    party: TravellerParty,
    occupancy: OccupancyAssessment,
    now: datetime,
) -> tuple[list[BuildProblem], Itinerary | None]:
    start = requirement.start_date
    if start is None:  # pragma: no cover - structural problems catch this
        raise AssertionError("assembly requires a start date")
    end = requirement.end_date
    day_count = (end - start).days + 1 if end is not None else 1
    destinations = tuple(requirement.destinations)
    stays = _stays(start, end, destinations) if end is not None else ()
    currency = requirement.budget_currency
    problems: list[BuildProblem] = []
    chosen: list[ItineraryComponent] = []

    def add(component: ItineraryComponent | None, problem: BuildProblem | None) -> None:
        if problem is not None:
            problems.append(problem)
        if component is not None:
            chosen.append(component)

    if ComponentType.FLIGHT in components:
        for origin, destination, travel_date in _flight_legs(requirement, stays, destinations, end):
            label = f"{origin} to {destination} on {travel_date.isoformat()}"
            selection = _pick(
                offers,
                component_type=ComponentType.FLIGHT,
                quantity=party.travellers,
                currency=currency,
                now=now,
                route=(origin, destination),
            )
            add(
                _component(selection, ComponentType.FLIGHT, travel_date, None, start, label),
                _selection_problem(selection, ComponentType.FLIGHT, label),
            )

    if ComponentType.HOTEL in components:
        for stay in stays:
            label = f"{stay.city} stay {stay.check_in.isoformat()} to {stay.check_out.isoformat()}"
            selection = _pick(
                offers,
                component_type=ComponentType.HOTEL,
                quantity=_booked_rooms(occupancy),
                currency=currency,
                now=now,
                city=stay.city,
            )
            add(
                _component(
                    selection,
                    ComponentType.HOTEL,
                    stay.check_in,
                    stay.check_out,
                    start,
                    label,
                ),
                _selection_problem(selection, ComponentType.HOTEL, label),
            )

    if ComponentType.TRANSFER in components:
        for stay in stays:
            label = f"{stay.city} arrival transfer on {stay.check_in.isoformat()}"
            selection = _pick(
                offers,
                component_type=ComponentType.TRANSFER,
                quantity=party.travellers,
                currency=currency,
                now=now,
                city=stay.city,
            )
            add(
                _component(selection, ComponentType.TRANSFER, stay.check_in, None, start, label),
                _selection_problem(selection, ComponentType.TRANSFER, label),
            )

    if ComponentType.ACTIVITY in components:
        for stay in stays:
            activity_date = stay.check_in + timedelta(days=1)
            if activity_date >= stay.check_out:
                activity_date = stay.check_in
            label = f"{stay.city} activity on {activity_date.isoformat()}"
            selection = _pick(
                offers,
                component_type=ComponentType.ACTIVITY,
                quantity=1,
                currency=currency,
                now=now,
                city=stay.city,
            )
            add(
                _component(selection, ComponentType.ACTIVITY, activity_date, None, start, label),
                _selection_problem(selection, ComponentType.ACTIVITY, label),
            )

    if ComponentType.VISA in components:
        visa_stops = stays or (_Stay(destinations[0], start, start, 0),)
        for stay in visa_stops:
            label = f"visa for {stay.city}"
            selection = _pick(
                offers,
                component_type=ComponentType.VISA,
                quantity=party.travellers,
                currency=currency,
                now=now,
                city=stay.city,
            )
            add(
                _component(selection, ComponentType.VISA, stay.check_in, None, start, label),
                _selection_problem(selection, ComponentType.VISA, label),
            )

    if problems:
        return problems, None

    plan = occupancy.plan
    if plan is None:  # pragma: no cover - feasible occupancy always has a plan
        raise AssertionError("assembly requires a feasible occupancy plan")

    ordered = tuple(
        sorted(
            chosen,
            key=lambda component: (
                component.day_index,
                _TYPE_ORDER[component.component_type],
                component.offer.supplier.casefold(),
                component.offer.offer_id,
            ),
        )
    )
    days = tuple(
        ItineraryDay(
            day_index=index,
            date=start + timedelta(days=index - 1),
            components=tuple(component for component in ordered if component.day_index == index),
        )
        for index in range(1, day_count + 1)
    )
    return (
        [],
        Itinerary(
            start_date=start,
            end_date=end or start,
            trip_kind=party.trip_kind(requirement.trip_type),
            party=party,
            occupancy=plan,
            currency=currency,
            days=days,
            components=ordered,
        ),
    )


def _booked_rooms(occupancy: OccupancyAssessment) -> int:
    plan = occupancy.plan
    if plan is None:  # pragma: no cover - feasible occupancy always has a plan
        raise AssertionError("a hotel itinerary needs a feasible occupancy plan")
    return plan.booked_rooms


def _flight_legs(
    requirement: Requirement,
    stays: tuple[_Stay, ...],
    destinations: tuple[str, ...],
    end: date | None,
) -> tuple[tuple[str, str, date], ...]:
    start = requirement.start_date
    if start is None:  # pragma: no cover - structural problems catch this
        raise AssertionError("flight legs need a start date")
    origin = requirement.origin_city
    if origin is None:  # pragma: no cover - structural problems catch this
        raise AssertionError("flight legs need an origin")
    legs = [(origin, destinations[0], start)]
    for index in range(1, len(destinations)):
        legs.append((destinations[index - 1], destinations[index], stays[index - 1].check_out))
    if end is not None:
        legs.append((destinations[-1], origin, end))
    return tuple(legs)


def _stays(start: date, end: date, destinations: tuple[str, ...]) -> tuple[_Stay, ...]:
    nights = (end - start).days
    if nights < len(destinations):
        return ()
    base, remainder = divmod(nights, len(destinations))
    stays: list[_Stay] = []
    cursor = start
    for index, city in enumerate(destinations):
        span = base + (1 if index < remainder else 0)
        check_out = cursor + timedelta(days=span)
        stays.append(_Stay(city=city, check_in=cursor, check_out=check_out, nights=span))
        cursor = check_out
    return tuple(stays)


def _pick(
    offers: tuple[Offer, ...],
    *,
    component_type: ComponentType,
    quantity: int,
    currency: str,
    now: datetime,
    city: str | None = None,
    route: tuple[str, str] | None = None,
) -> _Selection:
    scoped = [offer for offer in offers if offer.product_type == component_type]
    if route is not None:
        scoped = [offer for offer in scoped if _route_matches(offer.route, route[0], route[1])]
    elif city is not None:
        scoped = [offer for offer in scoped if _normalise(offer.city) == _normalise(city)]

    expired = sum(1 for offer in scoped if offer.expires_at <= now)
    unavailable = sum(
        1 for offer in scoped if offer.expires_at > now and offer.available_count < quantity
    )
    wrong_currency = sum(
        1
        for offer in scoped
        if offer.expires_at > now
        and offer.available_count >= quantity
        and offer.currency != currency
    )
    eligible = [
        offer
        for offer in scoped
        if offer.expires_at > now
        and offer.available_count >= quantity
        and offer.currency == currency
    ]
    if not eligible:
        return _Selection(
            offer=None,
            quantity=quantity,
            reason=None,
            considered=len(scoped),
            eligible=0,
            expired=expired,
            unavailable=unavailable,
            wrong_currency=wrong_currency,
        )

    best = min(
        eligible,
        key=lambda offer: (offer.total_paise, offer.supplier.casefold(), offer.offer_id),
    )
    reason = (
        SelectionReason.ONLY_AVAILABLE if len(eligible) == 1 else SelectionReason.CHEAPEST_AVAILABLE
    )
    return _Selection(
        offer=best,
        quantity=quantity,
        reason=reason,
        considered=len(scoped),
        eligible=len(eligible),
        expired=expired,
        unavailable=unavailable,
        wrong_currency=wrong_currency,
    )


def _component(
    selection: _Selection,
    component_type: ComponentType,
    start_date: date,
    end_date: date | None,
    trip_start: date,
    label: str,
) -> ItineraryComponent | None:
    offer = selection.offer
    reason = selection.reason
    if offer is None or reason is None:
        return None
    return ItineraryComponent(
        component_type=component_type,
        offer=offer,
        quantity=selection.quantity,
        unit_basis=_unit_basis(component_type),
        start_date=start_date,
        end_date=end_date,
        day_index=(start_date - trip_start).days + 1,
        reasons=(reason,),
        explanation=_explain(offer, selection, component_type, label),
    )


def _selection_problem(
    selection: _Selection,
    component_type: ComponentType,
    label: str,
) -> BuildProblem | None:
    if selection.offer is not None:
        return None
    details: list[str] = []
    if selection.expired:
        details.append(f"{selection.expired} expired")
    if selection.unavailable:
        details.append(f"{selection.unavailable} lacked availability")
    if selection.wrong_currency:
        details.append(f"{selection.wrong_currency} in another currency")
    if selection.considered == 0:
        detail = "no offers were supplied"
    elif details:
        detail = f"{selection.considered} considered: " + ", ".join(details)
    else:
        detail = f"{selection.considered} considered but none usable"
    return BuildProblem(
        code=ProblemCode.NO_OFFER,
        message=f"no available {component_type.value} offer for {label} ({detail})",
        component_type=component_type,
        location=label,
    )


def _explain(
    offer: Offer,
    selection: _Selection,
    component_type: ComponentType,
    label: str,
) -> str:
    total = Money(offer.total_paise * selection.quantity, offer.currency)
    if selection.reason is SelectionReason.ONLY_AVAILABLE:
        why = "the only available option"
    else:
        why = f"cheapest of {selection.eligible} available options"
    return (
        f"Chose {offer.supplier} {offer.name} [{offer.offer_id}] for {label} "
        f"at {total.format()} ({selection.quantity} {_unit_phrase(component_type, selection.quantity)}): "
        f"{why}."
    )


def _unit_phrase(component_type: ComponentType, quantity: int) -> str:
    if component_type is ComponentType.HOTEL:
        return "room" if quantity == 1 else "rooms"
    if component_type is ComponentType.ACTIVITY:
        return "group"
    return "traveller" if quantity == 1 else "travellers"


def _unit_basis(component_type: ComponentType) -> UnitBasis:
    if component_type is ComponentType.HOTEL:
        return UnitBasis.ROOM
    if component_type is ComponentType.ACTIVITY:
        return UnitBasis.GROUP
    return UnitBasis.TRAVELLER


def _route_matches(route: str | None, origin: str, destination: str) -> bool:
    if not route:
        return False
    parts = [
        part.strip()
        for part in _normalise(route).replace("→", "-").replace(" to ", "-").split("-")
        if part.strip()
    ]
    if len(parts) != 2:
        return False
    return parts[0] == _normalise(origin) and parts[1] == _normalise(destination)


def _normalise(value: str) -> str:
    return " ".join(value.split()).casefold()
