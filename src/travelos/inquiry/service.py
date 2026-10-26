"""Inquiry persistence, duplicate detection and the NEEDS_INFO loop (task 010c).

The parser produces a ``ParsedRequirement``; this service is the only place that
turns it into database state. Requirement versions are append-only, state moves
through the tested inquiry workflow machine, and a missing blocking field parks
the inquiry in NEEDS_INFO before any supplier search can start.

Clarifying questions are drafted deterministically and submitted through the
ActionPolicy layer (``message.clarifying_questions``, A1 by default). This module
never sends a message: it stores an approval request and a ``message_drafts`` row
for a human to review.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import timedelta
from typing import cast
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from travelos.approval.models import ApprovalRequest
from travelos.approval.service import ApprovalService, Submission
from travelos.channels.models import Conversation, Message
from travelos.core.audit import Actor, AuditWriter
from travelos.core.clock import Clock, SystemClock
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.models import HumanTask
from travelos.crm.fingerprints import ContactFingerprinter
from travelos.crm.models import Contact, Customer
from travelos.inquiry.errors import (
    InquiryNeedsInfoError,
    InquiryNoQuestionsError,
    InquiryNoRequirementError,
    InquiryNotFoundError,
    InquiryStateError,
    InquiryValidationError,
)
from travelos.inquiry.models import Inquiry, MessageDraft, RequirementRow
from travelos.inquiry.parser import ParseOutcome
from travelos.inquiry.questions import draft_body, question_codes
from travelos.inquiry.rules import (
    MissingField,
    Reason,
    Severity,
    missing_fields,
)
from travelos.workflow.service import WorkflowService

_CHANNELS = frozenset(
    {"whatsapp", "email", "sms", "phone", "instagram", "facebook", "google_business", "web_form"}
)
_REPARSEABLE = frozenset({"RECEIVED", "PARSED", "NEEDS_INFO"})
_CLOSED_STATES = frozenset({"COMPLETED", "CLOSED", "REJECTED", "EXPIRED", "LOST", "CANCELLED"})
_DUPLICATE_WINDOW = timedelta(days=30)
_TASK_TYPE = "inquiry.duplicate_suspected"


@dataclass(frozen=True, slots=True)
class DuplicateEvidence:
    """Why an earlier inquiry may be the same request. Contains no PII."""

    inquiry_id: UUID
    reasons: tuple[str, ...]
    channel: str | None
    days_apart: int

    def as_dict(self) -> dict[str, object]:
        return {
            "inquiry_id": str(self.inquiry_id),
            "reasons": list(self.reasons),
            "channel": self.channel,
            "days_apart": self.days_apart,
        }


@dataclass(frozen=True, slots=True)
class ParseRecord:
    """A stored requirement version plus the gaps and duplicate flags."""

    inquiry: Inquiry
    requirement: RequirementRow
    missing: tuple[MissingField, ...]
    duplicates: tuple[DuplicateEvidence, ...]
    requires_info: bool


@dataclass(frozen=True, slots=True)
class ClarificationDraft:
    """A draft awaiting a human decision; nothing is sent."""

    draft: MessageDraft
    approval: ApprovalRequest | None
    codes: tuple[str, ...]


class InquiryService:
    """Persistence and policy glue around the parsed requirement."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        audit: AuditWriter | None = None,
        workflow: WorkflowService | None = None,
        approvals: ApprovalService | None = None,
        fingerprinter: ContactFingerprinter | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._audit = audit or AuditWriter()
        self._workflow = workflow or WorkflowService(audit=self._audit)
        self._approvals = approvals or ApprovalService(clock=self._clock, audit=self._audit)
        self._fingerprinter = fingerprinter or ContactFingerprinter()

    # -- create and read --------------------------------------------------

    def create(
        self,
        db: Session,
        *,
        conversation_id: UUID | None = None,
        customer_id: UUID | None = None,
        source_id: UUID | None = None,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        actor_id: UUID | None = None,
    ) -> Inquiry:
        conversation: Conversation | None = None
        if conversation_id is not None:
            conversation = db.execute(
                select(Conversation).where(
                    Conversation.id == conversation_id,
                    Conversation.tenant_id == tenant_id,
                )
            ).scalar_one_or_none()
            if conversation is None:
                raise InquiryNotFoundError(f"conversation {conversation_id}")
            if conversation.customer_id is not None:
                if customer_id is not None and customer_id != conversation.customer_id:
                    raise InquiryValidationError("conversation belongs to a different customer")
                customer_id = conversation.customer_id
        if customer_id is not None:
            active = db.execute(
                select(Customer.id).where(
                    Customer.id == customer_id,
                    Customer.tenant_id == tenant_id,
                    Customer.merged_into_customer_id.is_(None),
                )
            ).scalar_one_or_none()
            if active is None:
                raise InquiryNotFoundError(f"customer {customer_id}")

        inquiry = Inquiry(
            tenant_id=tenant_id,
            customer_id=customer_id,
            conversation_id=conversation.id if conversation is not None else None,
            source_id=source_id,
            state="RECEIVED",
            trip_type="unknown",
            urgency="normal",
        )
        db.add(inquiry)
        db.flush()
        self._audit.record(
            db,
            action="inquiry.created",
            actor=Actor.user(actor_id) if actor_id is not None else Actor.system(),
            subject_type="inquiry",
            subject_id=inquiry.id,
            after={
                "channel": conversation.channel if conversation is not None else None,
                "conversation_id": conversation.id if conversation is not None else None,
                "customer_id": customer_id,
            },
        )
        return inquiry

    def get(self, db: Session, inquiry_id: UUID, *, tenant_id: UUID = DEFAULT_TENANT_ID) -> Inquiry:
        inquiry = db.execute(
            select(Inquiry).where(Inquiry.id == inquiry_id, Inquiry.tenant_id == tenant_id)
        ).scalar_one_or_none()
        if inquiry is None:
            raise InquiryNotFoundError(str(inquiry_id))
        return inquiry

    def list_inquiries(
        self,
        db: Session,
        *,
        state: str | None = None,
        customer_id: UUID | None = None,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        limit: int = 50,
    ) -> list[Inquiry]:
        statement = select(Inquiry).where(Inquiry.tenant_id == tenant_id)
        if state is not None:
            statement = statement.where(Inquiry.state == state)
        if customer_id is not None:
            statement = statement.where(Inquiry.customer_id == customer_id)
        return list(
            db.execute(
                statement.order_by(Inquiry.created_at.desc(), Inquiry.id.desc()).limit(
                    max(1, min(limit, 200))
                )
            )
            .scalars()
            .all()
        )

    def requirements(
        self, db: Session, inquiry_id: UUID, *, tenant_id: UUID = DEFAULT_TENANT_ID
    ) -> list[RequirementRow]:
        return list(
            db.execute(
                select(RequirementRow)
                .where(
                    RequirementRow.inquiry_id == inquiry_id,
                    RequirementRow.tenant_id == tenant_id,
                )
                .order_by(RequirementRow.version)
            )
            .scalars()
            .all()
        )

    def latest_requirement(
        self, db: Session, inquiry_id: UUID, *, tenant_id: UUID = DEFAULT_TENANT_ID
    ) -> RequirementRow:
        row = db.execute(
            select(RequirementRow)
            .where(
                RequirementRow.inquiry_id == inquiry_id,
                RequirementRow.tenant_id == tenant_id,
            )
            .order_by(RequirementRow.version.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            raise InquiryNoRequirementError(str(inquiry_id))
        return row

    def latest_inbound_message(
        self,
        db: Session,
        inquiry: Inquiry,
        *,
        message_id: UUID | None = None,
        tenant_id: UUID = DEFAULT_TENANT_ID,
    ) -> Message:
        """The message to parse: an explicit one, or the newest inbound in the thread."""

        if inquiry.conversation_id is None:
            raise InquiryValidationError("inquiry has no conversation to parse")
        statement = select(Message).where(
            Message.tenant_id == tenant_id,
            Message.conversation_id == inquiry.conversation_id,
            Message.direction == "inbound",
        )
        if message_id is not None:
            statement = statement.where(Message.id == message_id)
        else:
            statement = statement.order_by(Message.occurred_at.desc(), Message.id.desc()).limit(1)
        message = db.execute(statement).scalars().first()
        if message is None:
            raise InquiryNotFoundError("inbound message")
        return message

    # -- parse and persist ------------------------------------------------

    def record_parse(
        self,
        db: Session,
        *,
        inquiry_id: UUID,
        outcome: ParseOutcome,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        actor_id: UUID | None = None,
    ) -> ParseRecord:
        inquiry = self._locked(db, inquiry_id, tenant_id)
        if inquiry.state not in _REPARSEABLE:
            raise InquiryStateError(inquiry.id, inquiry.state)

        parsed = outcome.parsed
        parsed.check_no_silent_assumptions()
        requirement = parsed.requirement
        gaps = missing_fields(requirement, ambiguous_fields=parsed.ambiguous_fields)
        requires_info = any(gap.blocking for gap in gaps)

        version = self._next_version(db, inquiry.id, tenant_id)
        row = RequirementRow(
            tenant_id=tenant_id,
            inquiry_id=inquiry.id,
            version=version,
            data=requirement.to_json(),
            field_confidence=dict(parsed.confidence),
            source_spans={
                field: [span.model_dump() for span in spans]
                for field, spans in parsed.spans.items()
            },
            missing_fields=[gap.as_dict() for gap in gaps],
            prompt_name=outcome.prompt_name,
            prompt_version=outcome.prompt_version,
            provider=outcome.provider,
            model=outcome.model,
            llm_call_id=outcome.call_id,
        )
        db.add(row)
        db.flush()

        inquiry.trip_type = requirement.trip_type
        inquiry.urgency = requirement.urgency
        db.flush()

        if inquiry.state in {"RECEIVED", "NEEDS_INFO"}:
            self._transition(db, inquiry, "parse")
        if requires_info:
            self._transition(db, inquiry, "missing_info")

        duplicates = self.detect_duplicates(db, inquiry.id, tenant_id=tenant_id)
        if duplicates:
            inquiry.duplicate_of_inquiry_id = duplicates[0].inquiry_id
            inquiry.duplicate_evidence = [item.as_dict() for item in duplicates]
            db.add(
                HumanTask(
                    tenant_id=tenant_id,
                    task_type=_TASK_TYPE,
                    subject_type="inquiry",
                    subject_id=inquiry.id,
                    title=(
                        f"Possible duplicate inquiry ({len(duplicates)} earlier match"
                        f"{'es' if len(duplicates) != 1 else ''})"
                    ),
                    assignee_role="agent",
                    priority="normal",
                )
            )
            db.flush()

        self._audit.record(
            db,
            action="inquiry.parsed",
            actor=Actor.user(actor_id) if actor_id is not None else Actor.system(),
            subject_type="inquiry",
            subject_id=inquiry.id,
            after={
                "version": version,
                "prompt_name": outcome.prompt_name,
                "prompt_version": outcome.prompt_version,
                "provider": outcome.provider,
                "trip_type": requirement.trip_type,
                "requires_info": requires_info,
                "missing_fields": [gap.field for gap in gaps],
                "duplicates": len(duplicates),
            },
        )
        return ParseRecord(
            inquiry=inquiry,
            requirement=row,
            missing=gaps,
            duplicates=duplicates,
            requires_info=requires_info,
        )

    def assert_ready_for_search(
        self, db: Session, inquiry_id: UUID, *, tenant_id: UUID = DEFAULT_TENANT_ID
    ) -> Inquiry:
        """Fail closed when a blocking required field is still missing."""

        inquiry = self.get(db, inquiry_id, tenant_id=tenant_id)
        row = self.latest_requirement(db, inquiry_id, tenant_id=tenant_id)
        blocking = [item for item in _stored_gaps(row) if item.severity == "required"]
        if blocking:
            codes = ", ".join(item.field for item in blocking)
            raise InquiryNeedsInfoError(f"inquiry still needs required information: {codes}")
        return inquiry

    # -- qualifying questions ---------------------------------------------

    def draft_clarifying_questions(
        self,
        db: Session,
        *,
        inquiry_id: UUID,
        requested_by: UUID,
        channel: str | None = None,
        tenant_id: UUID = DEFAULT_TENANT_ID,
    ) -> ClarificationDraft:
        inquiry = self._locked(db, inquiry_id, tenant_id)
        if inquiry.state not in _REPARSEABLE:
            raise InquiryStateError(inquiry.id, inquiry.state)
        row = self.latest_requirement(db, inquiry.id, tenant_id=tenant_id)
        gaps = _stored_gaps(row)
        if not gaps:
            raise InquiryNoQuestionsError(str(inquiry.id))

        conversation: Conversation | None = None
        if inquiry.conversation_id is not None:
            conversation = db.get(Conversation, inquiry.conversation_id)
        resolved_channel = (
            channel or (conversation.channel if conversation else None) or ""
        ).strip()
        if resolved_channel not in _CHANNELS:
            raise InquiryValidationError("a supported channel is required to draft a message")
        if requested_by is None:  # defensive: the A1 approver needs a requester
            raise InquiryValidationError("a requester is required")

        body = draft_body(gaps)
        codes = question_codes(gaps)
        submission: Submission = self._approvals.submit(
            db,
            action_type="message.clarifying_questions",
            subject_type="inquiry",
            subject_id=inquiry.id,
            proposed={
                "inquiry_id": str(inquiry.id),
                "conversation_id": (str(conversation.id) if conversation is not None else None),
                "channel": resolved_channel,
                "body": body,
                "question_codes": list(codes),
            },
            context={
                "requirement_version": row.version,
                "missing_fields": [item.as_dict() for item in gaps],
            },
            requested_by=requested_by,
            tenant_id=tenant_id,
        )
        draft = MessageDraft(
            tenant_id=tenant_id,
            inquiry_id=inquiry.id,
            conversation_id=conversation.id if conversation is not None else None,
            contact_id=conversation.contact_id if conversation is not None else None,
            approval_id=submission.approval.id if submission.approval is not None else None,
            channel=resolved_channel,
            body=body,
            kind="clarification",
            question_codes=list(codes),
            status="draft",
            created_by=requested_by,
        )
        db.add(draft)
        db.flush()
        self._audit.record(
            db,
            action="inquiry.clarifying_questions_drafted",
            actor=Actor.user(requested_by),
            subject_type="inquiry",
            subject_id=inquiry.id,
            after={
                "draft_id": draft.id,
                "approval_id": submission.approval.id if submission.approval is not None else None,
                "level": submission.decision.level.value,
                "question_codes": list(codes),
            },
        )
        return ClarificationDraft(
            draft=draft,
            approval=submission.approval,
            codes=codes,
        )

    # -- duplicate detection ----------------------------------------------

    def detect_duplicates(
        self,
        db: Session,
        inquiry_id: UUID,
        *,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        window: timedelta = _DUPLICATE_WINDOW,
    ) -> tuple[DuplicateEvidence, ...]:
        """Deterministic, explainable duplicate candidates. Never merges."""

        inquiry = self.get(db, inquiry_id, tenant_id=tenant_id)
        now = self._clock.now()
        cutoff = now - window
        candidates = list(
            db.execute(
                select(Inquiry)
                .where(
                    Inquiry.tenant_id == tenant_id,
                    Inquiry.id != inquiry.id,
                    Inquiry.created_at >= cutoff,
                    Inquiry.state.not_in(_CLOSED_STATES),
                )
                .order_by(Inquiry.created_at.desc(), Inquiry.id)
            )
            .scalars()
            .all()
        )
        if not candidates:
            return ()

        conversations = self._conversations_for(db, candidates)
        candidate_channels: dict[UUID, str | None] = {}
        candidate_contacts: dict[UUID, UUID | None] = {}
        for candidate in candidates:
            conversation = (
                conversations.get(candidate.conversation_id)
                if candidate.conversation_id is not None
                else None
            )
            candidate_channels[candidate.id] = conversation.channel if conversation else None
            candidate_contacts[candidate.id] = conversation.contact_id if conversation else None

        hashes = self._contact_hashes(db, candidate_contacts.values(), tenant_id)
        source_hashes = self._source_hashes(db, inquiry, tenant_id)

        evidence: list[DuplicateEvidence] = []
        for candidate in candidates:
            reasons: list[str] = []
            if (
                inquiry.conversation_id is not None
                and candidate.conversation_id == inquiry.conversation_id
            ):
                reasons.append("same_conversation")
            if inquiry.customer_id is not None and candidate.customer_id == inquiry.customer_id:
                reasons.append("same_customer")
            contact_id = candidate_contacts[candidate.id]
            candidate_hash = hashes.get(contact_id) if contact_id is not None else None
            if candidate_hash is not None and candidate_hash in source_hashes:
                reasons.append("same_contact_fingerprint")
            if not reasons:
                continue
            created = candidate.created_at or now
            evidence.append(
                DuplicateEvidence(
                    inquiry_id=candidate.id,
                    reasons=tuple(reasons),
                    channel=candidate_channels[candidate.id],
                    days_apart=max(0, (now - created).days),
                )
            )
        return tuple(evidence[:5])

    # -- internal ---------------------------------------------------------

    def _locked(self, db: Session, inquiry_id: UUID, tenant_id: UUID) -> Inquiry:
        inquiry = db.execute(
            select(Inquiry)
            .where(Inquiry.id == inquiry_id, Inquiry.tenant_id == tenant_id)
            .with_for_update()
        ).scalar_one_or_none()
        if inquiry is None:
            raise InquiryNotFoundError(str(inquiry_id))
        return inquiry

    def _next_version(self, db: Session, inquiry_id: UUID, tenant_id: UUID) -> int:
        current = db.execute(
            select(func.coalesce(func.max(RequirementRow.version), 0)).where(
                RequirementRow.inquiry_id == inquiry_id,
                RequirementRow.tenant_id == tenant_id,
            )
        ).scalar_one()
        return int(current) + 1

    def _transition(self, db: Session, inquiry: Inquiry, trigger: str) -> None:
        result = self._workflow.apply(
            db,
            machine="inquiry",
            subject_type="inquiry",
            subject_id=inquiry.id,
            state=inquiry.state,
            trigger=trigger,
            payload={"inquiry_id": str(inquiry.id)},
            tenant_id=inquiry.tenant_id,
        )
        inquiry.state = result.target

    def _conversations_for(
        self, db: Session, inquiries: Iterable[Inquiry]
    ) -> dict[UUID, Conversation]:
        ids = {
            inquiry.conversation_id for inquiry in inquiries if inquiry.conversation_id is not None
        }
        if not ids:
            return {}
        rows = db.execute(select(Conversation).where(Conversation.id.in_(ids))).scalars()
        return {conversation.id: conversation for conversation in rows}

    def _contact_hashes(
        self, db: Session, contact_ids: Iterable[UUID | None], tenant_id: UUID
    ) -> dict[UUID, str | None]:
        ids = {contact_id for contact_id in contact_ids if contact_id is not None}
        if not ids:
            return {}
        rows = db.execute(
            select(Contact.id, Contact.value_hash).where(
                Contact.id.in_(ids), Contact.tenant_id == tenant_id
            )
        ).all()
        return {row.id: row.value_hash for row in rows}

    def _source_hashes(self, db: Session, inquiry: Inquiry, tenant_id: UUID) -> frozenset[str]:
        if inquiry.conversation_id is None:
            return frozenset()
        conversation = db.get(Conversation, inquiry.conversation_id)
        if conversation is None or conversation.contact_id is None:
            return frozenset()
        contact = db.get(Contact, conversation.contact_id)
        if contact is None or not contact.value_hash:
            return frozenset()
        return frozenset({contact.value_hash})


def match_customer_by_contact(
    db: Session,
    *,
    channel: str,
    value: str,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    fingerprinter: ContactFingerprinter | None = None,
) -> UUID | None:
    """Resolve a contact value to an active customer using keyed fingerprints."""

    normalized_channel = channel.strip().lower()
    if normalized_channel not in _CHANNELS:
        raise ValueError(f"unsupported channel: {channel!r}")
    if not value.strip():
        raise ValueError("contact value is required")
    fingerprint = (fingerprinter or ContactFingerprinter()).fingerprint(normalized_channel, value)
    return db.execute(
        select(Contact.customer_id)
        .join(Customer, Customer.id == Contact.customer_id)
        .where(
            Contact.tenant_id == tenant_id,
            Contact.channel == normalized_channel,
            Contact.value_hash == fingerprint,
            Customer.tenant_id == tenant_id,
            Customer.merged_into_customer_id.is_(None),
        )
        .limit(1)
    ).scalar_one_or_none()


def _stored_gaps(row: RequirementRow) -> tuple[MissingField, ...]:
    gaps: list[MissingField] = []
    for item in row.missing_fields:
        if not isinstance(item, Mapping):
            raise InquiryValidationError("stored missing_fields entry is not an object")
        gaps.append(
            MissingField(
                field=str(item.get("field", "")),
                severity=cast(Severity, str(item.get("severity", "required"))),
                reason=cast(Reason, str(item.get("reason", "missing"))),
                question_code=str(item.get("question_code", "")),
            )
        )
    return tuple(gaps)


__all__ = [
    "ClarificationDraft",
    "DuplicateEvidence",
    "InquiryService",
    "ParseRecord",
    "match_customer_by_contact",
]
