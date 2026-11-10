"""Expected quote failures.

A quote is only created from an ``ok`` price calculation, and every refusal names
the concrete reason so a member of staff knows whether to fix the price, wait for
an owner decision, or supply missing configuration.
"""

from __future__ import annotations

from travelos.core.errors import TravelOSError


class QuoteError(TravelOSError):
    """Base class for expected quote failures."""


class QuoteNotFoundError(QuoteError):
    """The quote is unknown in this tenant."""


class QuoteValidationError(QuoteError):
    """The calculation, itinerary or stored revision cannot be used as supplied."""


class QuotePricingError(QuoteValidationError):
    """The price calculation is missing, not ``ok``, or has no usable expiry."""


class QuoteGroupNotFoundError(QuoteError):
    """The quote group is unknown in this tenant."""


class QuoteGroupValidationError(QuoteValidationError):
    """The group mixes inquiries, reuses a quote or is otherwise invalid."""


class QuoteStateError(QuoteError):
    """The quote's workflow state does not allow the requested change."""
