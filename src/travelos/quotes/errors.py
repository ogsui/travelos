"""Expected quote failures.

A quote is only created from an ``ok`` price calculation, and every refusal names
the concrete reason so a member of staff knows whether to fix the price, wait for
an owner decision, or supply missing configuration.
"""

from __future__ import annotations

from uuid import UUID

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


class QuoteLinkNotFoundError(QuoteError):
    """The presented accept token does not resolve to exactly one quote."""


class QuoteAcceptanceError(QuoteStateError):
    """The quote cannot be accepted for the stated, customer-safe reason."""

    def __init__(self, quote_id: UUID, reason: str) -> None:
        self.quote_id = quote_id
        self.reason = reason
        super().__init__(f"quote {quote_id} cannot be accepted: {reason}")


class QuotePresentationError(QuoteError):
    """The owner's presentation rules are absent or cannot produce a document."""


class QuotePresentationConfigError(QuotePresentationError):
    """The presentation section is malformed or violates an invariant."""


class QuoteDocumentError(QuoteError):
    """A stored quote cannot be turned into a customer document."""


class QuotePdfUnavailableError(QuoteDocumentError):
    """PDF output was requested but the optional WeasyPrint adapter is absent."""
