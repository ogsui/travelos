"""Expected market-domain errors.

Messages are staff- and log-facing: they name the failed rule and never carry a
credential, cookie or provider session token.
"""

from __future__ import annotations

from travelos.core.errors import TravelOSError


class MarketError(TravelOSError):
    """Base class for expected market failures."""


class MarketValidationError(MarketError):
    """The caller supplied an inconsistent competitor-price request."""
