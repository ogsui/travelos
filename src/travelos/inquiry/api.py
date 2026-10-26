"""Authenticated inquiry endpoints (task 010c).

Parsing, duplicate flags and clarifying-question drafts are internal actions:
nothing here sends a customer message. The draft route only creates an approval
request (via the autonomy policy) and a draft row.
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from travelos.api.deps import (
    AuthContext,
    get_approval_service,
    get_audit_writer,
    get_db,
    require,
)
from travelos.approval.errors import ApprovalError, PolicyDeniedError
from travelos.approval.service import ApprovalService
from travelos.channels.models import Conversation
from travelos.core.audit import AuditWriter
from travelos.crm.models import Contact
from travelos.inquiry.errors import (
    InquiryNoQuestionsError,
    InquiryNoRequirementError,
    InquiryNotFoundError,
    InquiryStateError,
    InquiryValidationError,
    RequirementParseError,
    SilentAssumptionError,
)
from travelos.inquiry.models import Inquiry, RequirementRow
from travelos.inquiry.parser import InquiryParser
from travelos.inquiry.service import (
    ClarificationDraft,
    DuplicateEvidence,
    InquiryService,
    ParseRecord,
)
from travelos.llm.errors import LLMError
from travelos.security.permissions import Permission

router = APIRouter(prefix="/inquiries", tags=["inquiries"])


def get_inquiry_parser(request: Request) -> InquiryParser:
    parser: InquiryParser = request.app.state.inquiry_parser
    return parser


class InquiryCreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    conversation_id: UUID
    source_id: UUID | None = None


class ParseIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    message_id: UUID | None = None


class ClarifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Annotated[str | None, Field(default=None, min_length=2, max_length=20)]


class SpanOut(BaseModel):
    start: int
    end: int


class RequirementVersionOut(BaseModel):
    version: int
    data: dict[str, Any]
    field_confidence: dict[str, float]
    source_spans: dict[str, list[SpanOut]]
    missing_fields: list[dict[str, Any]]
    prompt_name: str | None
    prompt_version: int | None
    provider: str | None
    model: str | None
    created_at: datetime

    @classmethod
    def from_model(cls, row: RequirementRow) -> RequirementVersionOut:
        return cls(
            version=row.version,
            data=row.data,
            field_confidence=row.field_confidence,
            source_spans={
                field: [SpanOut.model_validate(span) for span in spans]
                for field, spans in row.source_spans.items()
            },
            missing_fields=row.missing_fields,
            prompt_name=row.prompt_name,
            prompt_version=row.prompt_version,
            provider=row.provider,
            model=row.model,
            created_at=row.created_at,
        )


class InquirySummaryOut(BaseModel):
    id: UUID
    customer_id: UUID | None
    conversation_id: UUID | None
    state: str
    trip_type: str
    urgency: str
    assigned_user_id: UUID | None
    duplicate_of_inquiry_id: UUID | None
    duplicate_evidence: list[dict[str, Any]]
    created_at: datetime

    @classmethod
    def from_model(cls, inquiry: Inquiry) -> InquirySummaryOut:
        return cls.model_validate(inquiry, from_attributes=True)


class InquiryOut(InquirySummaryOut):
    latest_requirement: RequirementVersionOut | None

    @classmethod
    def from_summary(
        cls,
        inquiry: Inquiry,
        *,
        latest: RequirementVersionOut | None = None,
    ) -> InquiryOut:
        return cls(
            **InquirySummaryOut.from_model(inquiry).model_dump(),
            latest_requirement=latest,
        )


class ParseOut(BaseModel):
    inquiry_id: UUID
    state: str
    version: int
    trip_type: str
    requires_info: bool
    missing_fields: list[dict[str, Any]]
    duplicates: list[dict[str, Any]]
    prompt_version: int | None


class DuplicateOut(BaseModel):
    inquiry_id: UUID
    reasons: list[str]
    channel: str | None
    days_apart: int

    @classmethod
    def from_evidence(cls, evidence: DuplicateEvidence) -> DuplicateOut:
        return cls(
            inquiry_id=evidence.inquiry_id,
            reasons=list(evidence.reasons),
            channel=evidence.channel,
            days_apart=evidence.days_apart,
        )


class ClarifyOut(BaseModel):
    draft_id: UUID
    approval_id: UUID | None
    approval_status: str | None
    channel: str
    question_codes: list[str]
    body: str
    sent: bool = False


def _service(audit: AuditWriter, approvals: ApprovalService) -> InquiryService:
    return InquiryService(audit=audit, approvals=approvals)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="inquiry_not_found")


def _conflict(code: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=code)


def _parse_failure() -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail="parse_failed")


@router.post("", response_model=InquiryOut, status_code=status.HTTP_201_CREATED)
def create_inquiry(
    payload: InquiryCreateIn,
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
    approvals: Annotated[ApprovalService, Depends(get_approval_service)],
) -> InquiryOut:
    service = _service(audit, approvals)
    try:
        inquiry = service.create(
            db,
            conversation_id=payload.conversation_id,
            source_id=payload.source_id,
            tenant_id=auth.user.tenant_id,
            actor_id=auth.user.id,
        )
    except InquiryNotFoundError as exc:
        raise _not_found() from exc
    except InquiryValidationError as exc:
        raise _conflict("conversation_mismatch") from exc
    db.commit()
    return InquiryOut.from_summary(inquiry)


@router.get("", response_model=list[InquirySummaryOut])
def list_inquiries(
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_READ))],
    db: Annotated[Session, Depends(get_db)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
    approvals: Annotated[ApprovalService, Depends(get_approval_service)],
    state_filter: Annotated[str | None, Query(alias="state", max_length=24)] = None,
    customer_id: UUID | None = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> list[InquirySummaryOut]:
    rows = _service(audit, approvals).list_inquiries(
        db,
        state=state_filter,
        customer_id=customer_id,
        tenant_id=auth.user.tenant_id,
        limit=limit,
    )
    return [InquirySummaryOut.from_model(row) for row in rows]


@router.get("/{inquiry_id}", response_model=InquiryOut)
def get_inquiry(
    inquiry_id: UUID,
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_READ))],
    db: Annotated[Session, Depends(get_db)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
    approvals: Annotated[ApprovalService, Depends(get_approval_service)],
) -> InquiryOut:
    service = _service(audit, approvals)
    try:
        inquiry = service.get(db, inquiry_id, tenant_id=auth.user.tenant_id)
    except InquiryNotFoundError as exc:
        raise _not_found() from exc
    versions = service.requirements(db, inquiry.id, tenant_id=auth.user.tenant_id)
    return InquiryOut.from_summary(
        inquiry,
        latest=RequirementVersionOut.from_model(versions[-1]) if versions else None,
    )


@router.post("/{inquiry_id}/parse", response_model=ParseOut)
def parse_inquiry(
    inquiry_id: UUID,
    payload: ParseIn,
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
    approvals: Annotated[ApprovalService, Depends(get_approval_service)],
    parser: Annotated[InquiryParser, Depends(get_inquiry_parser)],
) -> ParseOut:
    service = _service(audit, approvals)
    try:
        inquiry = service.get(db, inquiry_id, tenant_id=auth.user.tenant_id)
        message = service.latest_inbound_message(
            db, inquiry, message_id=payload.message_id, tenant_id=auth.user.tenant_id
        )
        conversation = (
            db.get(Conversation, message.conversation_id)
            if message.conversation_id is not None
            else None
        )
        contact = (
            db.get(Contact, conversation.contact_id)
            if conversation is not None and conversation.contact_id is not None
            else None
        )
        outcome = parser.parse(
            db,
            message=message.body,
            channel=message.channel,
            language_hint=contact.language
            if contact is not None and contact.language
            else "unknown",
            tenant_id=auth.user.tenant_id,
            actor_id=auth.user.id,
        )
        record = service.record_parse(
            db,
            inquiry_id=inquiry.id,
            outcome=outcome,
            tenant_id=auth.user.tenant_id,
            actor_id=auth.user.id,
        )
    except InquiryNotFoundError as exc:
        raise _not_found() from exc
    except (InquiryValidationError, InquiryStateError) as exc:
        raise _conflict("inquiry_state_conflict") from exc
    except (RequirementParseError, SilentAssumptionError) as exc:
        raise _parse_failure() from exc
    except LLMError as exc:
        raise HTTPException(
            status_code=status.HTTP_502_BAD_GATEWAY, detail="llm_unavailable"
        ) from exc
    db.commit()
    return _parse_out(record)


@router.post("/{inquiry_id}/clarify", response_model=ClarifyOut)
def clarify_inquiry(
    inquiry_id: UUID,
    payload: ClarifyIn,
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
    approvals: Annotated[ApprovalService, Depends(get_approval_service)],
) -> ClarifyOut:
    service = _service(audit, approvals)
    try:
        result = service.draft_clarifying_questions(
            db,
            inquiry_id=inquiry_id,
            requested_by=auth.user.id,
            channel=payload.channel,
            tenant_id=auth.user.tenant_id,
        )
    except InquiryNotFoundError as exc:
        raise _not_found() from exc
    except InquiryStateError as exc:
        raise _conflict("inquiry_state_conflict") from exc
    except (InquiryNoRequirementError, InquiryNoQuestionsError, InquiryValidationError) as exc:
        raise _conflict("no_questions_to_draft") from exc
    except PolicyDeniedError as exc:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="policy_denied") from exc
    except ApprovalError as exc:
        raise _conflict("approval_failed") from exc
    db.commit()
    return _clarify_out(result)


def _parse_out(record: ParseRecord) -> ParseOut:
    return ParseOut(
        inquiry_id=record.inquiry.id,
        state=record.inquiry.state,
        version=record.requirement.version,
        trip_type=record.inquiry.trip_type,
        requires_info=record.requires_info,
        missing_fields=[gap.as_dict() for gap in record.missing],
        duplicates=[
            DuplicateOut.from_evidence(item).model_dump(mode="json") for item in record.duplicates
        ],
        prompt_version=record.requirement.prompt_version,
    )


def _clarify_out(result: ClarificationDraft) -> ClarifyOut:
    return ClarifyOut(
        draft_id=result.draft.id,
        approval_id=result.draft.approval_id,
        approval_status=result.approval.status if result.approval is not None else None,
        channel=result.draft.channel,
        question_codes=list(result.draft.question_codes),
        body=result.draft.body,
        sent=False,
    )
