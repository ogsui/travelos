"""Deterministic occupancy and age rules (task 013a).

The numbers below are agency policy defaults, not supplier facts. They are
configurable through ``OccupancyPolicy`` and every rejection carries a code and a
readable reason. Changing a default is a policy change and belongs with the owner
(tracked in docs/BACKLOG.md).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from travelos.packages.models import (
    CHILD_MAX_AGE,
    INFANT_MAX_AGE,
    OccupancyAssessment,
    OccupancyProblem,
    ProblemCode,
    RoomAllocation,
    RoomPlan,
    RoomType,
    TravellerParty,
)


class OccupancyPolicy(BaseModel):
    """Owner-tunable room rules. Defaults are documented in docs/DECISIONS.md."""

    model_config = ConfigDict(frozen=True, extra="forbid", validate_default=True)

    max_adults_per_room: Annotated[int, Field(ge=1, le=6)] = 2
    max_children_per_room: Annotated[int, Field(ge=0, le=8)] = 2
    max_infants_per_room: Annotated[int, Field(ge=0, le=4)] = 1
    max_occupants_per_room: Annotated[int, Field(ge=1, le=12)] = 4
    max_rooms: Annotated[int, Field(ge=1, le=60)] = 30

    @model_validator(mode="after")
    def _capacity_covers_adults(self) -> OccupancyPolicy:
        if self.max_occupants_per_room < self.max_adults_per_room:
            raise ValueError("max occupants must be at least max adults")
        return self


DEFAULT_OCCUPANCY_POLICY = OccupancyPolicy()


def classify_age(age: int) -> Literal["infant", "child", "adult"]:
    """Infant under 2, child 2-11, adult 12 and over."""

    if age < 0:
        raise ValueError("age must not be negative")
    if age <= INFANT_MAX_AGE:
        return "infant"
    if age <= CHILD_MAX_AGE:
        return "child"
    return "adult"


@dataclass
class _Slot:
    adults: int = 1
    children: int = 0
    infants: int = 0

    @property
    def occupants(self) -> int:
        return self.adults + self.children + self.infants


def _pack(
    party: TravellerParty,
    rooms: int,
    policy: OccupancyPolicy,
) -> tuple[RoomAllocation, ...] | None:
    """Distribute the party over exactly ``rooms`` occupied rooms, or fail.

    Every room starts with one adult, so children are never unsupervised. The
    remaining adults, then children, then infants go to the room with the fewest
    of that kind and free capacity. The greedy choice is deterministic and the
    resulting plan is verified by the model invariants.
    """

    adults = party.adults_for_rooms
    if rooms < 1 or adults < rooms:
        return None

    slots = [_Slot() for _ in range(rooms)]
    for _ in range(adults - rooms):
        candidates = [
            slot
            for slot in slots
            if slot.adults < policy.max_adults_per_room
            and slot.occupants < policy.max_occupants_per_room
        ]
        if not candidates:
            return None
        min(candidates, key=lambda slot: (slot.adults, slot.occupants)).adults += 1

    for _ in range(party.young_children):
        candidates = [
            slot
            for slot in slots
            if slot.children < policy.max_children_per_room
            and slot.occupants < policy.max_occupants_per_room
        ]
        if not candidates:
            return None
        min(candidates, key=lambda slot: (slot.children, slot.occupants)).children += 1

    for _ in range(party.infants_for_rooms):
        candidates = [
            slot
            for slot in slots
            if slot.infants < policy.max_infants_per_room
            and slot.occupants < policy.max_occupants_per_room
        ]
        if not candidates:
            return None
        min(candidates, key=lambda slot: (slot.infants, slot.occupants)).infants += 1

    return tuple(
        RoomAllocation(
            room_type=_room_type(slot),
            adults=slot.adults,
            children=slot.children,
            infants=slot.infants,
        )
        for slot in slots
    )


def _room_type(slot: _Slot) -> RoomType:
    if slot.children or slot.infants:
        return RoomType.FAMILY
    if slot.adults == 1:
        return RoomType.SINGLE
    if slot.adults == 2:
        return RoomType.DOUBLE
    if slot.adults == 3:
        return RoomType.TRIPLE
    return RoomType.FAMILY


def _plural(count: int, singular: str, plural: str | None = None) -> str:
    if count == 1:
        return f"{count} {singular}"
    return f"{count} {plural or singular + 's'}"


def _infeasible(
    problems: list[OccupancyProblem],
    *,
    minimum_rooms: int | None = None,
) -> OccupancyAssessment:
    return OccupancyAssessment(
        feasible=False,
        minimum_rooms=minimum_rooms,
        plan=None,
        problems=tuple(problems),
    )


def assess_occupancy(
    party: TravellerParty,
    rooms: int | None = None,
    policy: OccupancyPolicy = DEFAULT_OCCUPANCY_POLICY,
) -> OccupancyAssessment:
    """Validate a party against the room policy and propose a room plan.

    ``rooms`` is the customer's/agent's room count. When omitted, the
    smallest feasible plan is booked. Extra requested rooms are booked but left
    unallocated; a request below the minimum is rejected with that minimum.
    """

    problems: list[OccupancyProblem] = []
    if party.adults == 0:
        problems.append(
            OccupancyProblem(
                code=ProblemCode.NO_ADULTS,
                message="an itinerary needs at least one adult traveller",
            )
        )
    if party.infants_for_rooms > party.adults_for_rooms:
        problems.append(
            OccupancyProblem(
                code=ProblemCode.INFANTS_PER_ADULT,
                message=(
                    f"{_plural(party.infants_for_rooms, 'infant')} need an adult each, "
                    f"but only {_plural(party.adults_for_rooms, 'adult')} are travelling"
                ),
            )
        )
    if rooms is not None and not 1 <= rooms <= policy.max_rooms:
        problems.append(
            OccupancyProblem(
                code=ProblemCode.ROOM_COUNT,
                message=f"room count must be between 1 and {policy.max_rooms}",
            )
        )
    if problems:
        return _infeasible(problems)

    minimum: int | None = None
    allocations: tuple[RoomAllocation, ...] | None = None
    for candidate in range(1, min(policy.max_rooms, party.adults_for_rooms) + 1):
        packed = _pack(party, candidate, policy)
        if packed is not None:
            minimum = candidate
            allocations = packed
            break

    if minimum is None or allocations is None:
        return _infeasible(
            [
                OccupancyProblem(
                    code=ProblemCode.TOO_FEW_ADULTS,
                    message=(
                        f"{_plural(party.young_children, 'child', 'children')} and "
                        f"{_plural(party.infants_for_rooms, 'infant')} cannot share rooms "
                        f"with only {_plural(party.adults_for_rooms, 'adult')}"
                    ),
                )
            ]
        )

    if rooms is not None and rooms < minimum:
        return _infeasible(
            [
                OccupancyProblem(
                    code=ProblemCode.ROOMS_BELOW_MINIMUM,
                    message=(
                        f"at least {_plural(minimum, 'room')} "
                        f"{'is' if minimum == 1 else 'are'} required for this party; "
                        f"{rooms} requested"
                    ),
                )
            ],
            minimum_rooms=minimum,
        )

    return OccupancyAssessment(
        feasible=True,
        minimum_rooms=minimum,
        plan=RoomPlan(
            booked_rooms=rooms if rooms is not None else minimum,
            allocations=allocations,
        ),
        problems=(),
    )
