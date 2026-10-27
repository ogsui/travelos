"""Pure itinerary and package domain models (task 013a).

An itinerary is built from immutable supplier ``Offer`` records plus explicit
occupancy and date rules. There is no database, LLM or network access here: a
later task persists the chosen offers by snapshot id, and the pricing engine
decides what the customer pays. This module therefore records supplier cost
only and never interprets a fare rule into a cancellation policy.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from enum import StrEnum
from typing import Annotated, Self

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from travelos.core.money import Money
from travelos.inquiry.requirement import Requirement
from travelos.suppliers.models import Offer


class ComponentType(StrEnum):
    """The component kinds an itinerary may contain."""

    FLIGHT = "flight"
    HOTEL = "hotel"
    TRANSFER = "transfer"
    ACTIVITY = "activity"
    VISA = "visa"


class UnitBasis(StrEnum):
    """What one unit of a component costs.

    This is the builder's documented reading of the normalized offer: a flight or
    transfer is priced per traveller, a hotel per room for the stay, and a group
    activity once for the party. A real supplier adapter must confirm its unit
    basis before booking.
    """

    TRAVELLER = "traveller"
    ROOM = "room"
    GROUP = "group"


class OptionTier(StrEnum):
    """The option tier an itinerary is persisted under.

    Task 013b1 only stores the label so a later tier-ranking slice can write
    value/standard/premium rows separately. The ranking rules do not exist yet.
    """

    VALUE = "value"
    STANDARD = "standard"
    PREMIUM = "premium"


class RoomType(StrEnum):
    SINGLE = "single"
    DOUBLE = "double"
    TRIPLE = "triple"
    FAMILY = "family"


class TripKind(StrEnum):
    FAMILY = "family"
    CORPORATE = "corporate"
    GROUP = "group"
    INDIVIDUAL = "individual"


class SelectionReason(StrEnum):
    """Why one offer was chosen among the eligible candidates."""

    ONLY_AVAILABLE = "only_available"
    CHEAPEST_AVAILABLE = "cheapest_available"


class ProblemCode(StrEnum):
    """A stable reason code for a rejected package or occupancy."""

    NO_ADULTS = "no_adults"
    INFANTS_PER_ADULT = "infants_per_adult"
    TOO_FEW_ADULTS = "too_few_adults"
    ROOMS_BELOW_MINIMUM = "rooms_below_minimum"
    ROOM_COUNT = "room_count"
    MISSING_ORIGIN = "missing_origin"
    MISSING_DESTINATION = "missing_destination"
    MISSING_START_DATE = "missing_start_date"
    MISSING_END_DATE = "missing_end_date"
    INVALID_DATE_RANGE = "invalid_date_range"
    STAY_TOO_SHORT = "stay_too_short"
    NO_OFFER = "no_offer"
    NO_COMPONENTS = "no_components"


def _as_utc(value: datetime) -> datetime:
    return value.astimezone(UTC)


UtcDateTime = Annotated[AwareDatetime, AfterValidator(_as_utc)]

INFANT_MAX_AGE = 1
CHILD_MAX_AGE = 11


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )


class TravellerParty(_FrozenModel):
    """Who is travelling, in the terms the occupancy rules need.

    ``children_ages`` keeps the parser's ages. Ages 12-17 share a room as adults
    but still count as children for fare purposes; ages 0-1 are infants for room
    capacity even when the parser recorded them in ``children_ages`` instead of
    the separate ``infants`` count.
    """

    adults: Annotated[int, Field(ge=0, le=60)] = 0
    children_ages: Annotated[
        tuple[Annotated[int, Field(ge=0, le=17)], ...],
        Field(max_length=8),
    ] = ()
    infants: Annotated[int, Field(ge=0, le=4)] = 0

    @classmethod
    def from_requirement(cls, requirement: Requirement) -> Self:
        return cls(
            adults=requirement.adults or 0,
            children_ages=tuple(requirement.children_ages),
            infants=requirement.infants,
        )

    @property
    def adults_for_rooms(self) -> int:
        """Adults plus children aged 12-17, who occupy a bed as an adult."""

        return self.adults + sum(1 for age in self.children_ages if age > CHILD_MAX_AGE)

    @property
    def young_children(self) -> int:
        """Children aged 2-11, who need a bed and supervision."""

        return sum(1 for age in self.children_ages if INFANT_MAX_AGE < age <= CHILD_MAX_AGE)

    @property
    def infants_for_rooms(self) -> int:
        """Declared infants plus any age 0-1 recorded in ``children_ages``."""

        return self.infants + sum(1 for age in self.children_ages if age <= INFANT_MAX_AGE)

    @property
    def travellers(self) -> int:
        """Everyone travelling, including infants."""

        return self.adults + len(self.children_ages) + self.infants

    def trip_kind(self, trip_type: str) -> TripKind:
        if trip_type == "corporate":
            return TripKind.CORPORATE
        if trip_type == "group":
            return TripKind.GROUP
        if self.children_ages or self.infants:
            return TripKind.FAMILY
        return TripKind.INDIVIDUAL


class RoomAllocation(_FrozenModel):
    """One occupied room. Extra booked rooms may stay unallocated."""

    room_type: RoomType
    adults: Annotated[int, Field(ge=1, le=6)]
    children: Annotated[int, Field(ge=0, le=8)] = 0
    infants: Annotated[int, Field(ge=0, le=4)] = 0

    @model_validator(mode="after")
    def _type_matches_occupants(self) -> Self:
        expected = _room_type_for(self.adults, self.children, self.infants)
        if self.room_type != expected:
            raise ValueError(f"room type must be {expected.value} for these occupants")
        return self


def _room_type_for(adults: int, children: int, infants: int) -> RoomType:
    if children or infants:
        return RoomType.FAMILY
    if adults == 1:
        return RoomType.SINGLE
    if adults == 2:
        return RoomType.DOUBLE
    if adults == 3:
        return RoomType.TRIPLE
    return RoomType.FAMILY


class RoomPlan(_FrozenModel):
    """The rooms to book and how the party is distributed across them."""

    booked_rooms: Annotated[int, Field(ge=1)]
    allocations: Annotated[tuple[RoomAllocation, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _allocations_fit_the_booking(self) -> Self:
        if len(self.allocations) > self.booked_rooms:
            raise ValueError("cannot allocate more rooms than are booked")
        return self


class OccupancyProblem(_FrozenModel):
    code: ProblemCode
    message: Annotated[str, Field(min_length=1)]


class OccupancyAssessment(_FrozenModel):
    feasible: bool
    minimum_rooms: Annotated[int | None, Field(ge=1)] = None
    plan: RoomPlan | None = None
    problems: tuple[OccupancyProblem, ...] = ()

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.feasible and (self.plan is None or self.problems):
            raise ValueError("a feasible assessment needs a plan and no problems")
        if not self.feasible and (self.plan is not None or not self.problems):
            raise ValueError("an infeasible assessment needs problems and no plan")
        return self


class ItineraryComponent(_FrozenModel):
    """One chosen supplier offer in the itinerary, with its explanation."""

    component_type: ComponentType
    offer: Offer
    quantity: Annotated[int, Field(ge=1)]
    unit_basis: UnitBasis
    start_date: date
    end_date: date | None = None
    day_index: Annotated[int, Field(ge=1)]
    reasons: Annotated[tuple[SelectionReason, ...], Field(min_length=1)]
    explanation: Annotated[str, Field(min_length=1)]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if self.offer.product_type != self.component_type:
            raise ValueError("offer product type must match the component type")
        if self.unit_basis != _unit_basis_for(self.component_type):
            raise ValueError("unit basis does not match the component type")
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("component end_date must not be before start_date")
        return self

    @property
    def cost(self) -> Money:
        """Supplier cost before any agency margin or tax interpretation."""

        return Money(self.offer.total_paise * self.quantity, self.offer.currency)


def _unit_basis_for(component_type: ComponentType) -> UnitBasis:
    if component_type is ComponentType.HOTEL:
        return UnitBasis.ROOM
    if component_type is ComponentType.ACTIVITY:
        return UnitBasis.GROUP
    return UnitBasis.TRAVELLER


class ItineraryDay(_FrozenModel):
    day_index: Annotated[int, Field(ge=1)]
    date: date
    components: tuple[ItineraryComponent, ...] = ()

    @model_validator(mode="after")
    def _components_belong_to_the_day(self) -> Self:
        if any(component.day_index != self.day_index for component in self.components):
            raise ValueError("every component must start on the day that lists it")
        return self


class Itinerary(_FrozenModel):
    """A deterministic, explainable set of chosen supplier components."""

    start_date: date
    end_date: date
    trip_kind: TripKind
    party: TravellerParty
    occupancy: RoomPlan
    currency: Annotated[str, Field(pattern=r"^[A-Z]{3}$")]
    days: Annotated[tuple[ItineraryDay, ...], Field(min_length=1)]
    components: Annotated[tuple[ItineraryComponent, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _calendar_and_components_agree(self) -> Self:
        if self.end_date < self.start_date:
            raise ValueError("end_date must not be before start_date")
        expected_days = (self.end_date - self.start_date).days + 1
        if len(self.days) != expected_days:
            raise ValueError("days must cover every calendar date in the trip")

        for index, day in enumerate(self.days, start=1):
            if day.day_index != index:
                raise ValueError("day indexes must start at 1 and be contiguous")
            if day.date != self.start_date + timedelta(days=index - 1):
                raise ValueError("day dates must be contiguous from start_date")

        if any(component.offer.currency != self.currency for component in self.components):
            raise ValueError("every component must use the itinerary currency")
        for component in self.components:
            if component.day_index > len(self.days):
                raise ValueError("component day_index falls outside the trip")
            if not self.start_date <= component.start_date <= self.end_date:
                raise ValueError("component start_date falls outside the trip")
            listed = self.days[component.day_index - 1].components
            if component not in listed:
                raise ValueError("every component must appear on its day")
        if sum(len(day.components) for day in self.days) != len(self.components):
            raise ValueError("a component cannot be listed twice")
        return self

    @property
    def total_cost(self) -> Money:
        total = Money(0, self.currency)
        for component in self.components:
            total = total + component.cost
        return total

    @property
    def earliest_expiry(self) -> datetime:
        """The first supplier price/availability deadline in the itinerary."""

        return self.supplier_deadlines[0].expires_at

    @property
    def supplier_deadlines(self) -> tuple[SupplierDeadline, ...]:
        """Every chosen offer's price/availability deadline, soonest first."""

        deadlines = (
            SupplierDeadline(
                supplier=component.offer.supplier,
                offer_id=component.offer.offer_id,
                component_type=component.component_type,
                expires_at=component.offer.expires_at,
            )
            for component in self.components
        )
        return tuple(
            sorted(
                deadlines,
                key=lambda deadline: (
                    deadline.expires_at,
                    deadline.supplier.casefold(),
                    deadline.offer_id,
                ),
            )
        )

    @property
    def explanations(self) -> tuple[str, ...]:
        return tuple(component.explanation for component in self.components)


class SupplierDeadline(_FrozenModel):
    """A supplier's own price/availability deadline for a chosen component."""

    supplier: Annotated[str, Field(min_length=1, max_length=200)]
    offer_id: Annotated[str, Field(min_length=1, max_length=200)]
    component_type: ComponentType
    expires_at: UtcDateTime


class BuildProblem(_FrozenModel):
    """A rejection reason the caller can show without guessing."""

    code: ProblemCode
    message: Annotated[str, Field(min_length=1)]
    component_type: ComponentType | None = None
    location: str | None = None


class PackageBuild(_FrozenModel):
    """Either a complete itinerary or the reasons one cannot be built."""

    itinerary: Itinerary | None = None
    problems: tuple[BuildProblem, ...] = ()

    @model_validator(mode="after")
    def _one_outcome(self) -> Self:
        if self.itinerary is None and not self.problems:
            raise ValueError("a failed build must explain why")
        if self.itinerary is not None and self.problems:
            raise ValueError("a successful build cannot carry problems")
        return self

    @property
    def ok(self) -> bool:
        return self.itinerary is not None
