"""Approval-gated sales drafts (task 011a).

The service has two entry points:

* ``draft_acknowledgement`` uses an owner-approved, claim-free template.
* ``draft_reply`` accepts staff- or agent-written text and runs the claim guard
  before anything is stored.

Both create an encrypted ``message_drafts`` row and submit the matching approval
action (``message.instant_ack`` or ``message.sales_reply``). No sender exists, so
nothing leaves the system; the draft body is kept out of the approval payload to
avoid storing customer text unencrypted.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.approval.models import ApprovalRequest
from travelos.approval.service import ApprovalService, Submission
from travelos.channels.models import Conversation
from travelos.conversation.errors import (
    SalesClosedError,
    SalesNotFoundError,
    SalesValidationError,
)
from travelos.conversation.guard import GuardReport, require_supported
from travelos.conversation.templates import (
    ACKNOWLEDGEMENT_TEMPLATES,
    MessageTemplate,
    TemplateRegistry,
)
from travelos.core.audit import Actor, AuditWriter
from travelos.core.clock import Clock, SystemClock
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.ids import uuid7
from travelos.crm.models import Contact
from travelos.inquiry.models import Inquiry, MessageDraft

_ACK_ACTION: Final = "message.instant_ack"
_REPLY_ACTION: Final = "message.sales_reply"
_SUPPORTED_CHANNELS: Final[frozenset[str]] = frozenset(
    {"whatsapp", "email", "sms", "phone", "instagram", "facebook", "google_business", "web_form"}
)
_CLOSED_STATES: Final[frozenset[str]] = frozenset(
    {"CLOSED", "CANCELLED", "REJECTED", "LOST", "EXPIRED"}
)
_MAX_BODY_CHARS: Final = 4000


@dataclass(frozen=True, slots=True)
class ReplyDraft:
    """A stored draft, its approval request and the guard result."""

    draft: MessageDraft
    approval: ApprovalRequest | None
    guard: GuardReport
    template_key: str | None


class SalesReplyService:
    """Draft customer replies for approval. Never sends and never calls an LLM."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        audit: AuditWriter | None = None,
        approvals: ApprovalService | None = None,
        templates: Sequence[MessageTemplate] | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._audit = audit or AuditWriter()
        self._approvals = approvals or ApprovalService(clock=self._clock, audit=self._audit)
        self._templates = TemplateRegistry(
            templates if templates is not None else ACKNOWLEDGEMENT_TEMPLATES
        )

    # -- public API -------------------------------------------------------

    def draft_acknowledgement(
        self,
        db: Session,
        *,
        inquiry_id: UUID,
        requested_by: UUID,
        channel: str | None = None,
        tenant_id: UUID = DEFAULT_TENANT_ID,
    ) -> ReplyDraft:
        """Draft the owner-approved acknowledgement for an inquiry."""

        inquiry = self._inquiry(db, inquiry_id, tenant_id)
        conversation, resolved_channel, language = self._context(db, inquiry, tenant_id, channel)
        template = self._templates.acknowledgement(language)
        self._templates.require_approved(template)
        report = require_supported(template.body)
        return self._store(
            db,
            inquiry=inquiry,
            conversation=conversation,
            channel=resolved_channel,
            requested_by=requested_by,
            tenant_id=tenant_id,
            body=template.body,
            kind="acknowledgement",
            template_key=template.key,
            action_type=_ACK_ACTION,
            report=report,
        )

    def draft_reply(
        self,
        db: Session,
        *,
        inquiry_id: UUID,
        requested_by: UUID,
        body: str,
        sources: dict[str, Sequence[str]] | None = None,
        channel: str | None = None,
        tenant_id: UUID = DEFAULT_TENANT_ID,
    ) -> ReplyDraft:
        """Draft free-form reply text after checking every claim against a source."""

        inquiry = self._inquiry(db, inquiry_id, tenant_id)
        conversation, resolved_channel, _language = self._context(db, inquiry, tenant_id, channel)
        text = body.strip()
        if not text:
            raise SalesValidationError("a reply body is required")
        if len(text) > _MAX_BODY_CHARS:
            raise SalesValidationError(f"a reply body must be at most {_MAX_BODY_CHARS} characters")
        report = require_supported(text, sources)
        return self._store(
            db,
            inquiry=inquiry,
            conversation=conversation,
            channel=resolved_channel,
            requested_by=requested_by,
            tenant_id=tenant_id,
            body=text,
            kind="sales_reply",
            template_key=None,
            action_type=_REPLY_ACTION,
            report=report,
        )

    # -- internals --------------------------------------------------------

    def _inquiry(self, db: Session, inquiry_id: UUID, tenant_id: UUID) -> Inquiry:
        inquiry = db.execute(
            select(Inquiry).where(Inquiry.id == inquiry_id, Inquiry.tenant_id == tenant_id)
        ).scalar_one_or_none()
        if inquiry is None:
            raise SalesNotFoundError(f"inquiry {inquiry_id}")
        if inquiry.state in _CLOSED_STATES:
            raise SalesClosedError(inquiry.state)
        return inquiry

    def _context(
        self,
        db: Session,
        inquiry: Inquiry,
        tenant_id: UUID,
        channel: str | None,
    ) -> tuple[Conversation, str, str]:
        if inquiry.conversation_id is None:
            raise SalesValidationError("an inquiry without a conversation cannot be replied to")
        conversation = db.execute(
            select(Conversation).where(
                Conversation.id == inquiry.conversation_id,
                Conversation.tenant_id == tenant_id,
            )
        ).scalar_one_or_none()
        if conversation is None:
            raise SalesNotFoundError(f"conversation {inquiry.conversation_id}")
        resolved = (channel or conversation.channel).strip().lower()
        if resolved not in _SUPPORTED_CHANNELS:
            raise SalesValidationError(f"unsupported channel: {resolved!r}")
        language = "en"
        if conversation.contact_id is not None:
            contact = db.execute(
                select(Contact).where(
                    Contact.id == conversation.contact_id,
                    Contact.tenant_id == tenant_id,
                )
            ).scalar_one_or_none()
            if contact is not None and contact.language:
                language = contact.language
        return conversation, resolved, language

    def _store(
        self,
        db: Session,
        *,
        inquiry: Inquiry,
        conversation: Conversation,
        channel: str,
        requested_by: UUID,
        tenant_id: UUID,
        body: str,
        kind: str,
        template_key: str | None,
        action_type: str,
        report: GuardReport,
    ) -> ReplyDraft:
        draft_id = uuid7()
        submission: Submission = self._approvals.submit(
            db,
            action_type=action_type,
            subject_type="inquiry",
            subject_id=inquiry.id,
            proposed={
                "draft_id": str(draft_id),
                "inquiry_id": str(inquiry.id),
                "conversation_id": str(conversation.id),
                "channel": channel,
                "kind": kind,
                "template_key": template_key,
            },
            context={
                "inquiry_state": inquiry.state,
                "guard": {
                    "claims": list(report.kinds()),
                    "unsupported": list(report.unsupported_kinds()),
                    "sources": {key: list(refs) for key, refs in sorted(report.sources.items())},
                },
            },
            requested_by=requested_by,
            tenant_id=tenant_id,
        )
        draft = MessageDraft(
            id=draft_id,
            tenant_id=tenant_id,
            inquiry_id=inquiry.id,
            conversation_id=conversation.id,
            contact_id=conversation.contact_id,
            approval_id=submission.approval.id if submission.approval is not None else None,
            channel=channel,
            body=body,
            question_codes=[],
            kind=kind,
            template_key=template_key,
            status="draft",
            created_by=requested_by,
        )
        db.add(draft)
        db.flush()
        self._audit.record(
            db,
            action=(
                "sales.acknowledgement_drafted"
                if kind == "acknowledgement"
                else "sales.reply_drafted"
            ),
            actor=Actor.user(requested_by),
            subject_type="inquiry",
            subject_id=inquiry.id,
            after={
                "draft_id": draft.id,
                "approval_id": submission.approval.id if submission.approval is not None else None,
                "level": submission.decision.level.value,
                "kind": kind,
                "template_key": template_key,
                "claim_kinds": list(report.kinds()),
                "source_kinds": sorted(report.sources),
            },
        )
        return ReplyDraft(
            draft=draft,
            approval=submission.approval,
            guard=report,
            template_key=template_key,
        )
