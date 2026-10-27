"""Occupancy and age rules for itinerary packages (task 013a).

The defaults are explicit, configurable policy values, not supplier facts. They
are pinned here so a change is visible in review and in the audit trail of tests.
"""

from __future__ import annotations

from travelos.inquiry.requirement import Requirement
from travelos.packages.models import ProblemCode, RoomType, TravellerParty
from travelos.packages.occupancy import OccupancyPolicy, assess_occupancy, classify_age


def party(
    *,
    adults: int = 2,
    children: tuple[int, ...] = (),
    infants: int = 0,
) -> TravellerParty:
    return TravellerParty(adults=adults, children_ages=children, infants=infants)


def requirement(
    *,
    adults: int | None = 2,
    children_ages: list[int] | None = None,
    rooms: int | None = 1,
) -> Requirement:
    return Requirement(
        trip_type="package",
        origin_city="Delhi",
        destinations=["Goa"],
        adults=adults,
        children_ages=children_ages or [],
        rooms=rooms,
    )


def test_age_boundaries_are_part_of_the_shared_rule() -> None:
    assert [classify_age(age) for age in (0, 1, 2, 11, 12, 17)] == [
        "infant",
        "infant",
        "child",
        "child",
        "adult",
        "adult",
    ]


def test_two_adults_and_one_child_need_one_family_room_but_book_two() -> None:
    assessment = assess_occupancy(party(adults=2, children=(7,)), rooms=2)

    assert assessment.feasible is True
    assert assessment.minimum_rooms == 1
    assert assessment.problems == ()
    plan = assessment.plan
    assert plan is not None
    assert plan.booked_rooms == 2
    assert plan.allocations[0].room_type == "family"
    assert (plan.allocations[0].adults, plan.allocations[0].children) == (2, 1)


def test_three_children_need_two_adults_for_two_rooms() -> None:
    assessment = assess_occupancy(party(adults=2, children=(3, 5, 7)), rooms=1)

    assert assessment.feasible is False
    assert assessment.plan is None
    assert assessment.problems[0].code is ProblemCode.ROOMS_BELOW_MINIMUM
    assert "at least 2 rooms" in assessment.problems[0].message


def test_three_children_and_two_adults_are_feasible_in_two_rooms() -> None:
    assessment = assess_occupancy(party(adults=2, children=(3, 5, 7)))

    assert assessment.feasible is True
    assert assessment.minimum_rooms == 2
    plan = assessment.plan
    assert plan is not None
    assert plan.booked_rooms == 2
    assert sum(room.children for room in plan.allocations) == 3


def test_one_adult_cannot_supervise_two_rooms() -> None:
    assessment = assess_occupancy(party(adults=1, children=(3, 5, 7)), rooms=2)

    assert assessment.feasible is False
    assert assessment.problems[0].code is ProblemCode.TOO_FEW_ADULTS
    assert "1 adult" in assessment.problems[0].message


def test_every_infant_needs_an_adult() -> None:
    assessment = assess_occupancy(party(adults=1, infants=2), rooms=2)

    assert assessment.feasible is False
    assert assessment.problems[0].code is ProblemCode.INFANTS_PER_ADULT


def test_an_infant_age_inside_children_ages_counts_as_an_infant() -> None:
    assessment = assess_occupancy(party(adults=1, children=(1,)), rooms=1)

    assert assessment.feasible is True
    plan = assessment.plan
    assert plan is not None
    assert (plan.allocations[0].children, plan.allocations[0].infants) == (0, 1)
    assert plan.allocations[0].room_type == "family"


def test_no_adult_is_rejected_before_rooms_are_considered() -> None:
    assessment = assess_occupancy(party(adults=0, children=(8,)), rooms=1)

    assert assessment.feasible is False
    assert [problem.code for problem in assessment.problems] == [ProblemCode.NO_ADULTS]


def test_more_rooms_than_policy_allows_is_rejected() -> None:
    assessment = assess_occupancy(party(adults=2), rooms=31)

    assert assessment.feasible is False
    assert assessment.problems[0].code is ProblemCode.ROOM_COUNT


def test_six_adults_share_three_double_rooms() -> None:
    assessment = assess_occupancy(party(adults=6), rooms=3)

    assert assessment.feasible is True
    assert assessment.minimum_rooms == 3
    plan = assessment.plan
    assert plan is not None
    assert [room.room_type for room in plan.allocations] == ["double", "double", "double"]


def test_unspecified_rooms_use_the_minimum() -> None:
    assessment = assess_occupancy(party(adults=2))

    assert assessment.feasible is True
    plan = assessment.plan
    assert plan is not None
    assert plan.booked_rooms == 1
    assert plan.allocations[0].room_type == "double"


def test_an_older_child_shares_as_an_adult_but_still_pays_as_a_child() -> None:
    parsed = requirement(adults=1, children_ages=[16], rooms=1)
    travellers = TravellerParty.from_requirement(parsed)

    assert travellers.adults == 1
    assert travellers.adults_for_rooms == 2
    assert travellers.travellers == 2
    assessment = assess_occupancy(travellers, 1)
    assert assessment.feasible is True
    plan = assessment.plan
    assert plan is not None
    assert plan.allocations[0].room_type == "double"
    assert plan.allocations[0].children == 0


def test_policy_can_be_tightened_without_changing_the_models() -> None:
    strict = OccupancyPolicy(max_adults_per_room=1)
    assessment = assess_occupancy(party(adults=2), rooms=1, policy=strict)

    assert assessment.feasible is False
    assert assessment.problems[0].code is ProblemCode.ROOMS_BELOW_MINIMUM


def test_trip_kind_is_derived_from_composition_and_declared_type() -> None:
    assert party(adults=2).trip_kind("package") == "individual"
    assert party(adults=2, children=(4,)).trip_kind("package") == "family"
    assert party(adults=2).trip_kind("corporate") == "corporate"
    assert party(adults=2).trip_kind("group") == "group"
    assert party(adults=2).trip_kind("visa") == "individual"


def test_room_type_is_derived_from_occupants() -> None:
    assert RoomType.SINGLE == "single"
    assert RoomType.DOUBLE == "double"
    assert RoomType.TRIPLE == "triple"
    assert RoomType.FAMILY == "family"
