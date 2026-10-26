"""Requirement schema, parser and missing-info rules (task 010)."""

from travelos.inquiry.errors import (
    DuplicateInquiryError,
    InquiryError,
    InquiryNeedsInfoError,
    InquiryNoQuestionsError,
    InquiryNoRequirementError,
    InquiryNotFoundError,
    InquiryStateError,
    InquiryValidationError,
    RequirementParseError,
    SilentAssumptionError,
)
from travelos.inquiry.models import Inquiry, MessageDraft, RequirementRow
from travelos.inquiry.parser import LANGUAGE_HINTS, PROMPT_NAME, InquiryParser, ParseOutcome
from travelos.inquiry.questions import draft_body, question_for
from travelos.inquiry.requirement import (
    REQUIREMENT_FIELDS,
    ParsedRequirement,
    Requirement,
    RequirementField,
    SourceSpan,
)
from travelos.inquiry.rules import MissingField, is_ready, missing_fields, required_fields
from travelos.inquiry.service import (
    ClarificationDraft,
    DuplicateEvidence,
    InquiryService,
    ParseRecord,
    match_customer_by_contact,
)

__all__ = [
    "LANGUAGE_HINTS",
    "PROMPT_NAME",
    "REQUIREMENT_FIELDS",
    "ClarificationDraft",
    "DuplicateEvidence",
    "DuplicateInquiryError",
    "Inquiry",
    "InquiryError",
    "InquiryNeedsInfoError",
    "InquiryNoQuestionsError",
    "InquiryNoRequirementError",
    "InquiryNotFoundError",
    "InquiryParser",
    "InquiryService",
    "InquiryStateError",
    "InquiryValidationError",
    "MessageDraft",
    "MissingField",
    "ParseOutcome",
    "ParseRecord",
    "ParsedRequirement",
    "Requirement",
    "RequirementField",
    "RequirementParseError",
    "RequirementRow",
    "SilentAssumptionError",
    "SourceSpan",
    "draft_body",
    "is_ready",
    "match_customer_by_contact",
    "missing_fields",
    "question_for",
    "required_fields",
]
