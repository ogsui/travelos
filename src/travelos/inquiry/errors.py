"""Expected inquiry-domain errors."""

from __future__ import annotations

from travelos.core.errors import TravelOSError


class InquiryError(TravelOSError):
    """Base class for expected inquiry failures."""


class SilentAssumptionError(InquiryError):
    """The parser asserted a field the customer never stated."""


class RequirementParseError(InquiryError):
    """The model output could not be turned into a schema-valid requirement."""


class DuplicateInquiryError(InquiryError):
    """A likely duplicate needs a human decision before a second inquiry is opened."""
