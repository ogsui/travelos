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


class InquiryNotFoundError(InquiryError):
    """The inquiry, conversation or message does not exist in this tenant."""


class InquiryValidationError(InquiryError):
    """The caller supplied an inconsistent inquiry or message reference."""


class InquiryStateError(InquiryError):
    """The inquiry workflow does not allow this action in its current state."""

    def __init__(self, inquiry_id: object, state: str) -> None:
        self.inquiry_id = inquiry_id
        self.state = state
        super().__init__(f"inquiry {inquiry_id} is in state {state}")


class InquiryNoRequirementError(InquiryError):
    """The inquiry has no parsed requirement version yet."""


class InquiryNoQuestionsError(InquiryError):
    """Nothing is missing, so there is no clarifying question to draft."""


class InquiryNeedsInfoError(InquiryError):
    """A blocking required field is still missing; supplier search must wait."""
