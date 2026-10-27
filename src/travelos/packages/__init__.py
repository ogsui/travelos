"""Deterministic itinerary and package assembly (task 013a)."""

from travelos.packages.builder import build_package
from travelos.packages.models import (
    BuildProblem,
    ComponentType,
    Itinerary,
    ItineraryComponent,
    ItineraryDay,
    OccupancyAssessment,
    OccupancyProblem,
    PackageBuild,
    ProblemCode,
    RoomAllocation,
    RoomPlan,
    RoomType,
    SelectionReason,
    SupplierDeadline,
    TravellerParty,
    TripKind,
    UnitBasis,
)
from travelos.packages.occupancy import (
    DEFAULT_OCCUPANCY_POLICY,
    OccupancyPolicy,
    assess_occupancy,
    classify_age,
)

__all__ = [
    "DEFAULT_OCCUPANCY_POLICY",
    "BuildProblem",
    "ComponentType",
    "Itinerary",
    "ItineraryComponent",
    "ItineraryDay",
    "OccupancyAssessment",
    "OccupancyPolicy",
    "OccupancyProblem",
    "PackageBuild",
    "ProblemCode",
    "RoomAllocation",
    "RoomPlan",
    "RoomType",
    "SelectionReason",
    "SupplierDeadline",
    "TravellerParty",
    "TripKind",
    "UnitBasis",
    "assess_occupancy",
    "build_package",
    "classify_age",
]
