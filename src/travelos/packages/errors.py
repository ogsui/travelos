"""Expected package-persistence errors.

The store refuses a link it cannot prove, so a quote can never point at another
supplier's price or at a snapshot that was never recorded.
"""

from __future__ import annotations

from travelos.core.errors import TravelOSError


class PackageError(TravelOSError):
    """Base class for expected package failures."""


class ItineraryNotFoundError(PackageError):
    """The itinerary is unknown in this tenant."""


class PackagePersistenceError(PackageError):
    """The itinerary or its snapshot links are not usable as supplied."""
