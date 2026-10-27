"""Deterministic itinerary assembly, occupancy rules and persistence (task 013)."""

from travelos.packages.builder import build_package
from travelos.packages.errors import (
    ItineraryNotFoundError,
    PackageError,
    PackagePersistenceError,
)
from travelos.packages.models import (
    BuildProblem,
    ComponentType,
    Itinerary,
    ItineraryComponent,
    ItineraryDay,
    OccupancyAssessment,
    OccupancyProblem,
    OptionTier,
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
from travelos.packages.records import ItineraryItemRecord, ItineraryRecord
from travelos.packages.store import ItineraryStore

__all__ = [
    "DEFAULT_OCCUPANCY_POLICY",
    "BuildProblem",
    "ComponentType",
    "Itinerary",
    "ItineraryComponent",
    "ItineraryDay",
    "ItineraryItemRecord",
    "ItineraryNotFoundError",
    "ItineraryRecord",
    "ItineraryStore",
    "OccupancyAssessment",
    "OccupancyPolicy",
    "OccupancyProblem",
    "OptionTier",
    "PackageBuild",
    "PackageError",
    "PackagePersistenceError",
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
