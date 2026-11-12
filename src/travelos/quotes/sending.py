"""Approval-gated quote sending (task 016c2a).

Sending a quote is a state change with a customer-visible effect, so it is split
into two explicit steps:

* :meth:`QuoteSendService.request` checks that the quote can be offered at all,
  renders nothing, and asks the approval layer for ``quote.send`` (A1 by default,
  i.e. one human decision). A denied policy decision refuses before any write.
* :meth:`QuoteSendService.deliver` re-checks every gate, requires that the
  approved request is for *this* quote and *this* revision, renders the customer
  document from the owner's presentation rules and only then hands it to a
  transport.

Rendering is not sending. The body is the same fail-closed document the renderer
produces: a missing owner brand/terms/payment config refuses the send instead of
inventing wording, and no price, tax, margin or recipient is computed here.

Safety gates, evaluated in this order on both steps:

1. quote state, current revision and the stored expiry window;
2. owner presentation configuration (``NEEDS_CONFIG`` refuses);
3. the channel kill switch (feature flag, OFF by default);
4. contact consent for that channel and purpose (missing consent denies);
5. the approval decision (auto, pending, approved, expired or denied);
6. mode: ``test`` and ``shadow`` run the whole pipeline and record that the send
   was suppressed, but never call a transport. Only ``live`` delivers.

The caller owns the transaction. Nothing here schedules a retry, stores the
rendered document or invents a customer message template: those belong to the
delivery adapter and to the owner's wording.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.approval.errors import ApprovalNotFoundError
from travelos.approval.matrix import Decision
from travelos.approval.service import ApprovalService
from travelos.core.audit import Actor, AuditWriter
from travelos.core.config import Mode
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.money import Money
from travelos.quotes.errors import (
    QuoteNotFoundError,
    QuotePresentationError,
    QuoteSendError,
    QuoteValidationError,
)
from travelos.quotes.events import QuoteEventStore
from travelos.quotes.models import QuoteEventKind, QuoteState
from travelos.quotes.presentation import DEFAULT_PRESENTATION_PATH
from travelos.quotes.records import QuoteRecord
from travelos.quotes.rendering import QuoteRenderer, RenderedQuote, require_presentation
from travelos.quotes.store import QuoteStore

SEND_ACTION = "quote.send"
# A quote can be offered on the channels that have both a kill switch and a
# delivery adapter slot. Adding a channel is a code change, not a config value.
SEND_CHANNELS = frozenset({"email", "whatsapp"})
_REQUESTABLE = frozenset({QuoteState.DRAFT})
_DELIVERABLE = frozenset({QuoteState.PENDING_APPROVAL, QuoteState.APPROVED})


class KillSwitchReader(Protocol):
    """Reads the effective channel kill switch (operator AND owner)."""

    def is_enabled(self, key: str, *, tenant_id: UUID) -> bool: ...


class ConsentReader(Protocol):
    """Answers whether a contact may receive this channel/purpose now."""

    def can_send(
        self,
        db: Session,
        *,
        contact_id: UUID,
        channel: str,
        purpose: str,
        tenant_id: UUID,
    ) -> bool: ...


class QuoteTransport(Protocol):
    """Delivers one rendered quote. It never calculates or re-renders anything."""

    def send(
        self,
        *,
        quote_id: UUID,
        revision_number: int,
        channel: str,
        recipient: str,
        html: str,
        idempotency_key: str,
    ) -> str:
        """Return the transport's external id for this delivery."""
        ...


@dataclass(frozen=True, slots=True)
class SendRequest:
    """The result of asking for permission to send one revision."""

    quote_id: UUID
    approval_id: UUID | None
    requires_approval: bool
    revision_number: int
    total: Money
    expires_at: datetime
    channel: str


@dataclass(frozen=True, slots=True)
class SendOutcome:
    """What happened when delivery was attempted.

    ``sent`` is true only when a transport accepted the document. ``suppressed``
    names the mode that stopped it, so a shadow run is explicit rather than
    silently successful.
    """

    quote_id: UUID
    revision_number: int
    total: Money
    channel: str
    sent: bool
    suppressed: str | None
    external_id: str | None
    at: datetime


class QuoteSendService:
    """Requests and performs the approval-gated delivery of one quote."""

    def __init__(
        self,
        db: Session,
        *,
        mode: Mode = Mode.TEST,
        flags: KillSwitchReader | None = None,
        consent: ConsentReader | None = None,
        approvals: ApprovalService | None = None,
        transport: QuoteTransport | None = None,
        audit: AuditWriter | None = None,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        presentation_path: Path | str = DEFAULT_PRESENTATION_PATH,
    ) -> None:
        self._db = db
        self._mode = mode
        self._flags = flags
        self._consent = consent
        self._approvals = approvals or ApprovalService()
        self._transport = transport
        self._audit = audit or AuditWriter()
        self._tenant_id = tenant_id
        self._presentation_path = presentation_path
        self._renderer = QuoteRenderer(
            db,
            tenant_id=tenant_id,
            audit=self._audit,
            presentation_path=presentation_path,
        )

    # ------------------------------------------------------------------
    # Step 1: approval
    # ------------------------------------------------------------------

    def request(
        self,
        quote_id: UUID,
        *,
        channel: str,
        contact_id: UUID,
        purpose: str,
        actor: Actor,
        now: datetime,
    ) -> SendRequest:
        """Check that the quote can be offered and open the send approval."""

        instant = _aware(now, "now")
        _require_channel(channel)
        quote = self._lock(quote_id)
        state = QuoteState(quote.state)
        if state not in _REQUESTABLE:
            raise QuoteSendError(quote_id, f"not_draft:{state.value}")
        expires_at = _aware(quote.expires_at, "quote expiry")
        if instant >= expires_at:
            raise QuoteSendError(quote_id, "expired")

        total = self._current_total(quote)
        self._require_presentation()
        self._require_delivery_gates(
            quote_id, channel=channel, contact_id=contact_id, purpose=purpose
        )

        submission = self._approvals.submit(
            self._db,
            action_type=SEND_ACTION,
            subject_type="quote",
            subject_id=quote.id,
            proposed={
                "revision_number": quote.current_revision,
                "total_paise": total.paise,
                "currency": total.currency,
                "expires_at": expires_at,
                "channel": channel,
                "purpose": purpose,
            },
            requested_by=actor.id,
            value_paise=total.paise,
            tenant_id=self._tenant_id,
        )
        quote.state = QuoteState.PENDING_APPROVAL.value
        self._db.flush()
        self._audit.record(
            self._db,
            action="quote.send.requested",
            actor=actor,
            subject_type="quote",
            subject_id=quote.id,
            after={
                "approval_id": submission.approval.id if submission.approval else None,
                "auto_approved": submission.approval is None,
                "revision_number": quote.current_revision,
                "total_paise": total.paise,
                "currency": total.currency,
                "channel": channel,
                "purpose": purpose,
                "expires_at": expires_at,
            },
        )
        self._db.flush()
        return SendRequest(
            quote_id=quote.id,
            approval_id=None if submission.approval is None else submission.approval.id,
            requires_approval=submission.approval is not None,
            revision_number=quote.current_revision,
            total=total,
            expires_at=expires_at,
            channel=channel,
        )

    # ------------------------------------------------------------------
    # Step 2: delivery
    # ------------------------------------------------------------------

    def deliver(
        self,
        quote_id: UUID,
        *,
        approval_id: UUID | None,
        channel: str,
        contact_id: UUID,
        purpose: str,
        recipient: str,
        actor: Actor,
        now: datetime,
    ) -> SendOutcome:
        """Re-check every gate, then deliver, suppress or refuse."""

        instant = _aware(now, "now")
        _require_channel(channel)
        if not recipient.strip():
            raise QuoteValidationError("a quote can only be delivered to a recipient")
        quote = self._lock(quote_id)
        state = QuoteState(quote.state)
        if state not in _DELIVERABLE:
            raise QuoteSendError(quote_id, f"not_sendable:{state.value}")
        expires_at = _aware(quote.expires_at, "quote expiry")
        if instant >= expires_at:
            raise QuoteSendError(quote_id, "expired")

        total = self._current_total(quote)
        self._require_delivery_gates(
            quote_id, channel=channel, contact_id=contact_id, purpose=purpose
        )
        self._require_approval(quote_id, quote, approval_id=approval_id, total=total)

        rendered = self._render(quote, actor=actor, now=instant)
        option = rendered.document.options[0]
        if Money(option.total.paise, option.total.currency) != total:
            raise QuoteValidationError(
                f"quote {quote_id} rendered a total that is not the stored revision total"
            )

        if state is QuoteState.PENDING_APPROVAL:
            quote.state = QuoteState.APPROVED.value
            self._db.flush()
            self._audit.record(
                self._db,
                action="quote.approved",
                actor=actor,
                subject_type="quote",
                subject_id=quote.id,
                after={
                    "approval_id": approval_id,
                    "revision_number": quote.current_revision,
                    "total_paise": total.paise,
                    "currency": total.currency,
                },
            )
            self._db.flush()

        if self._mode is not Mode.LIVE:
            self._audit.record(
                self._db,
                action="quote.send.suppressed",
                actor=actor,
                subject_type="quote",
                subject_id=quote.id,
                after={
                    "mode": self._mode.value,
                    "channel": channel,
                    "purpose": purpose,
                    "approval_id": approval_id,
                    "revision_number": quote.current_revision,
                    "total_paise": total.paise,
                    "currency": total.currency,
                },
            )
            self._db.flush()
            return SendOutcome(
                quote_id=quote.id,
                revision_number=quote.current_revision,
                total=total,
                channel=channel,
                sent=False,
                suppressed=self._mode.value,
                external_id=None,
                at=instant,
            )

        if self._transport is None:
            raise QuoteSendError(quote_id, "transport_not_configured")
        try:
            external_id = self._transport.send(
                quote_id=quote.id,
                revision_number=quote.current_revision,
                channel=channel,
                recipient=recipient.strip(),
                html=rendered.html,
                idempotency_key=(f"quote:{quote.id}:r{quote.current_revision}:{channel}"),
            )
        except Exception as exc:  # the transport decides its own retry policy
            raise QuoteSendError(quote_id, "transport_failed") from exc

        quote.state = QuoteState.SENT.value
        self._db.flush()
        QuoteEventStore(self._db, tenant_id=quote.tenant_id).record(
            quote,
            kind=QuoteEventKind.SENT,
            revision_number=quote.current_revision,
            total=total,
            actor_type="staff",
            at=instant,
        )
        self._audit.record(
            self._db,
            action="quote.sent",
            actor=actor,
            subject_type="quote",
            subject_id=quote.id,
            after={
                "approval_id": approval_id,
                "external_id": external_id,
                "channel": channel,
                "purpose": purpose,
                "revision_number": quote.current_revision,
                "total_paise": total.paise,
                "currency": total.currency,
            },
        )
        self._db.flush()
        return SendOutcome(
            quote_id=quote.id,
            revision_number=quote.current_revision,
            total=total,
            channel=channel,
            sent=True,
            suppressed=None,
            external_id=external_id,
            at=instant,
        )

    # ------------------------------------------------------------------
    # Gates
    # ------------------------------------------------------------------

    def _require_presentation(self) -> None:
        require_presentation(self._presentation_path)

    def _render(self, quote: QuoteRecord, *, actor: Actor, now: datetime) -> RenderedQuote:
        try:
            return self._renderer.render_quote(quote.id, now=now, actor=actor)
        except QuotePresentationError:
            raise
        except Exception as exc:
            raise QuoteValidationError(
                f"quote {quote.id} cannot be rendered for sending: {exc}"
            ) from exc

    def _require_delivery_gates(
        self,
        quote_id: UUID,
        *,
        channel: str,
        contact_id: UUID,
        purpose: str,
    ) -> None:
        if self._flags is None or not self._flags.is_enabled(channel, tenant_id=self._tenant_id):
            raise QuoteSendError(quote_id, f"kill_switch:{channel}")
        if self._consent is None:
            raise QuoteSendError(quote_id, "consent_not_configured")
        if not self._consent.can_send(
            self._db,
            contact_id=contact_id,
            channel=channel,
            purpose=purpose,
            tenant_id=self._tenant_id,
        ):
            raise QuoteSendError(quote_id, f"no_consent:{channel}:{purpose}")

    def _require_approval(
        self,
        quote_id: UUID,
        quote: QuoteRecord,
        *,
        approval_id: UUID | None,
        total: Money,
    ) -> None:
        decision = self._approvals.autonomy.decision_for(
            self._db,
            SEND_ACTION,
            value_paise=total.paise,
            tenant_id=self._tenant_id,
        )
        if decision.decision is Decision.DENY:
            raise QuoteSendError(quote_id, "policy_denied")
        if decision.decision is Decision.AUTO:
            return
        if approval_id is None:
            raise QuoteSendError(quote_id, "approval_required")
        try:
            request = self._approvals.get(self._db, approval_id, tenant_id=self._tenant_id)
        except ApprovalNotFoundError:
            raise QuoteSendError(quote_id, "approval_not_found") from None
        if request.status != "approved":
            raise QuoteSendError(quote_id, f"approval_{request.status}")
        if (
            request.action_type != SEND_ACTION
            or request.subject_type != "quote"
            or request.subject_id != quote.id
        ):
            raise QuoteSendError(quote_id, "approval_mismatch")
        approved_revision = request.proposed.get("revision_number")
        if approved_revision != quote.current_revision:
            raise QuoteSendError(quote_id, "stale_approval")

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _lock(self, quote_id: UUID) -> QuoteRecord:
        quote = self._db.execute(
            select(QuoteRecord)
            .where(
                QuoteRecord.id == quote_id,
                QuoteRecord.tenant_id == self._tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if quote is None:
            raise QuoteNotFoundError(f"quote {quote_id} was not found")
        return quote

    def _current_total(self, quote: QuoteRecord) -> Money:
        # QuoteStore.load verifies that every line adds up to the stored total.
        return QuoteStore(self._db, tenant_id=self._tenant_id).load(quote.id).current.total


def _require_channel(channel: str) -> None:
    if channel not in SEND_CHANNELS:
        raise QuoteValidationError(
            f"quotes cannot be sent on {channel!r}; expected one of "
            + ", ".join(sorted(SEND_CHANNELS))
        )


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise QuoteValidationError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


__all__ = [
    "SEND_ACTION",
    "SEND_CHANNELS",
    "ConsentReader",
    "KillSwitchReader",
    "QuoteSendService",
    "QuoteTransport",
    "SendOutcome",
    "SendRequest",
]
