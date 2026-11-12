"""Approval-gated quote sending (task 016c2a).

Sending is split into a request step (which asks the approval layer for
``quote.send``) and a delivery step (which re-checks every gate and either
delivers, suppresses by mode or refuses). These tests drive both steps against
stored rows:

* the request moves a draft quote to ``PENDING_APPROVAL`` and records the exact
  revision, total, channel and purpose on the approval, with no contact data;
* a non-draft, expired, kill-switched, unconsented or unconfigured quote is
  refused before anything is written;
* delivery requires an approved request for *this* quote, refuses a pending
  request, a withdrawn consent and a live deployment with no transport;
* ``test``/``shadow`` run the full pipeline and record a suppressed send without
  calling a transport or writing a ``sent`` event;
* ``live`` hands the rendered document to the transport exactly once, moves the
  quote to ``SENT``, appends the ``sent`` timeline entry and audits it.

Every customer, address, brand, price and purpose here is a synthetic
engineering fixture.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from quote_fixtures import NOW, make_quote_for, make_user, write_presentation_config
from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.approval.service import ApprovalService, ReviewDecision
from travelos.core.audit import Actor
from travelos.core.clock import ManualClock
from travelos.core.config import Mode
from travelos.core.models import AuditLog
from travelos.crm.consent import ConsentService
from travelos.crm.models import Consent, Contact
from travelos.crm.service import CRMService
from travelos.quotes.errors import (
    QuotePresentationError,
    QuoteSendError,
    QuoteValidationError,
)
from travelos.quotes.events import QuoteEventStore
from travelos.quotes.models import QuoteEventKind, QuoteState
from travelos.quotes.records import QuoteRecord
from travelos.quotes.sending import QuoteSendService, SendRequest
from travelos.quotes.store import QuoteStore

pytestmark = pytest.mark.integration

TTL = timedelta(hours=24)
ESCALATION = timedelta(minutes=30)
CHANNEL = "email"
PURPOSE = "transactional"


class Flags:
    """A stand-in for the effective channel kill switches."""

    def __init__(self, *enabled: str) -> None:
        self.enabled = frozenset(enabled)

    def is_enabled(self, key: str, *, tenant_id: UUID) -> bool:
        return key in self.enabled


class RecordingTransport:
    """Captures what a real transport would have received; sends nothing."""

    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

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
        self.calls.append(
            {
                "quote_id": quote_id,
                "revision_number": revision_number,
                "channel": channel,
                "recipient": recipient,
                "html": html,
                "idempotency_key": idempotency_key,
            }
        )
        return f"synthetic-transport-{len(self.calls)}"


def service(
    db: Session,
    path: Path,
    *,
    mode: Mode = Mode.TEST,
    flags: Flags | None = None,
    consent: ConsentService | None = None,
    transport: RecordingTransport | None = None,
) -> QuoteSendService:
    return QuoteSendService(
        db,
        mode=mode,
        flags=Flags(CHANNEL) if flags is None else flags,
        consent=consent or ConsentService(),
        approvals=ApprovalService(clock=ManualClock(NOW), ttl=TTL, escalation=ESCALATION),
        transport=transport,
        presentation_path=path,
    )


def make_contact(db: Session, *, grant: bool = True) -> Contact:
    crm = CRMService()
    customer = crm.create_customer(
        db,
        customer_type="individual",
        display_name="Synthetic Customer",
        segment=None,
        actor=Actor.system(),
    )
    contact = crm.create_contact(
        db,
        customer_id=customer.id,
        channel=CHANNEL,
        value=f"customer-{uuid4().hex[:8]}@example.test",
        verified=True,
        is_primary=True,
        language="en",
        actor=Actor.system(),
    )
    if grant:
        ConsentService().grant(
            db,
            contact_id=contact.id,
            channel=CHANNEL,
            purpose=PURPOSE,
            source="synthetic-test",
            proof_ref=None,
            actor=Actor.system(),
            now=NOW,
        )
    return contact


def approvals(db: Session) -> ApprovalService:
    return ApprovalService(clock=ManualClock(NOW), ttl=TTL, escalation=ESCALATION)


def requested(
    db: Session,
    path: Path,
    *,
    quote: QuoteRecord,
    contact: Contact,
    flags: Flags | None = None,
) -> tuple[QuoteSendService, SendRequest]:
    requester = make_user(db)
    svc = service(db, path, flags=flags)
    request = svc.request(
        quote.id,
        channel=CHANNEL,
        contact_id=contact.id,
        purpose=PURPOSE,
        actor=Actor.user(requester.id),
        now=NOW,
    )
    db.flush()
    return svc, request


def approve(db: Session, request_id: UUID) -> None:
    reviewer = make_user(db)
    approvals(db).decide(
        db,
        request_id=request_id,
        reviewer_id=reviewer.id,
        reviewer_role="owner",
        decision=ReviewDecision.APPROVE,
    )
    db.flush()


# ---------------------------------------------------------------------------
# Requesting permission to send
# ---------------------------------------------------------------------------


def test_request_opens_a_send_approval_without_contact_data(db: Session, tmp_path: Path) -> None:
    quote = make_quote_for(db, None)
    contact = make_contact(db)

    _, request = requested(db, write_presentation_config(tmp_path), quote=quote, contact=contact)

    assert request.requires_approval is True
    assert request.approval_id is not None
    assert request.revision_number == 1
    assert request.total == QuoteStore(db).load(quote.id).current.total
    assert quote.state == QuoteState.PENDING_APPROVAL.value

    approval = approvals(db).get(db, request.approval_id)
    assert approval.action_type == "quote.send"
    assert approval.subject_type == "quote"
    assert approval.subject_id == quote.id
    assert approval.proposed["revision_number"] == 1
    assert approval.proposed["total_paise"] == request.total.paise
    assert approval.proposed["channel"] == CHANNEL
    assert approval.proposed["purpose"] == PURPOSE
    assert "contact_id" not in approval.proposed
    assert str(contact.id) not in str(approval.proposed)

    row = db.execute(select(AuditLog).where(AuditLog.action == "quote.send.requested")).scalar_one()
    assert row.after is not None
    assert row.after["approval_id"] == str(request.approval_id)
    assert row.after["revision_number"] == 1
    assert "token" not in row.after


def test_a_quote_that_is_not_a_draft_cannot_request_a_send(db: Session, tmp_path: Path) -> None:
    quote = make_quote_for(db, None)
    contact = make_contact(db)
    path = write_presentation_config(tmp_path)
    svc, _ = requested(db, path, quote=quote, contact=contact)

    with pytest.raises(QuoteSendError, match="not_draft:PENDING_APPROVAL"):
        svc.request(
            quote.id,
            channel=CHANNEL,
            contact_id=contact.id,
            purpose=PURPOSE,
            actor=Actor.system(),
            now=NOW,
        )

    rows = db.execute(select(AuditLog).where(AuditLog.action == "quote.send.requested")).scalars()
    assert len(list(rows)) == 1


def test_an_expired_quote_cannot_request_a_send(db: Session, tmp_path: Path) -> None:
    quote = make_quote_for(db, None)
    contact = make_contact(db)

    with pytest.raises(QuoteSendError, match="expired"):
        service(db, write_presentation_config(tmp_path)).request(
            quote.id,
            channel=CHANNEL,
            contact_id=contact.id,
            purpose=PURPOSE,
            actor=Actor.system(),
            now=quote.expires_at,
        )

    assert quote.state == QuoteState.DRAFT.value


def test_request_refuses_a_channel_whose_kill_switch_is_off(db: Session, tmp_path: Path) -> None:
    quote = make_quote_for(db, None)
    contact = make_contact(db)

    with pytest.raises(QuoteSendError, match="kill_switch:email"):
        service(db, write_presentation_config(tmp_path), flags=Flags()).request(
            quote.id,
            channel=CHANNEL,
            contact_id=contact.id,
            purpose=PURPOSE,
            actor=Actor.system(),
            now=NOW,
        )

    assert quote.state == QuoteState.DRAFT.value


def test_request_refuses_a_contact_without_consent(db: Session, tmp_path: Path) -> None:
    quote = make_quote_for(db, None)
    contact = make_contact(db, grant=False)

    with pytest.raises(QuoteSendError, match="no_consent:email:transactional"):
        service(db, write_presentation_config(tmp_path)).request(
            quote.id,
            channel=CHANNEL,
            contact_id=contact.id,
            purpose=PURPOSE,
            actor=Actor.system(),
            now=NOW,
        )

    assert quote.state == QuoteState.DRAFT.value


def test_request_refuses_an_incomplete_owner_presentation(db: Session, tmp_path: Path) -> None:
    quote = make_quote_for(db, None)
    contact = make_contact(db)

    def blank(data: dict) -> None:
        data["quote_presentation"]["terms"]["text"] = None

    path = write_presentation_config(tmp_path, mutate=blank)
    with pytest.raises(QuotePresentationError):
        service(db, path).request(
            quote.id,
            channel=CHANNEL,
            contact_id=contact.id,
            purpose=PURPOSE,
            actor=Actor.system(),
            now=NOW,
        )

    assert quote.state == QuoteState.DRAFT.value
    assert (
        db.execute(
            select(AuditLog).where(AuditLog.action == "quote.send.requested")
        ).scalar_one_or_none()
        is None
    )


def test_request_refuses_a_channel_with_no_send_path(db: Session, tmp_path: Path) -> None:
    quote = make_quote_for(db, None)
    contact = make_contact(db)

    with pytest.raises(QuoteValidationError, match="cannot be sent on"):
        service(db, write_presentation_config(tmp_path)).request(
            quote.id,
            channel="carrier_pigeon",
            contact_id=contact.id,
            purpose=PURPOSE,
            actor=Actor.system(),
            now=NOW,
        )


# ---------------------------------------------------------------------------
# Delivery
# ---------------------------------------------------------------------------


def test_test_mode_runs_the_pipeline_and_suppresses_the_send(db: Session, tmp_path: Path) -> None:
    quote = make_quote_for(db, None)
    contact = make_contact(db)
    transport = RecordingTransport()
    svc, request = requested(db, write_presentation_config(tmp_path), quote=quote, contact=contact)
    approve(db, request.approval_id)

    outcome = svc.deliver(
        quote.id,
        approval_id=request.approval_id,
        channel=CHANNEL,
        contact_id=contact.id,
        purpose=PURPOSE,
        recipient="synthetic@example.test",
        actor=Actor.system(),
        now=NOW,
    )

    assert outcome.sent is False
    assert outcome.suppressed == "test"
    assert outcome.external_id is None
    assert transport.calls == []
    assert quote.state == QuoteState.APPROVED.value
    assert QuoteEventStore(db).timeline(quote.id) == ()

    actions = set(
        db.execute(
            select(AuditLog.action).where(
                AuditLog.action.in_(["quote.approved", "quote.send.suppressed"])
            )
        ).scalars()
    )
    assert actions == {"quote.approved", "quote.send.suppressed"}


def test_live_delivery_sends_the_rendered_document_once(db: Session, tmp_path: Path) -> None:
    quote = make_quote_for(db, None)
    contact = make_contact(db)
    transport = RecordingTransport()
    path = write_presentation_config(tmp_path)
    _svc, request = requested(db, path, quote=quote, contact=contact)
    approve(db, request.approval_id)
    live = service(db, path, mode=Mode.LIVE, transport=transport)

    outcome = live.deliver(
        quote.id,
        approval_id=request.approval_id,
        channel=CHANNEL,
        contact_id=contact.id,
        purpose=PURPOSE,
        recipient="synthetic@example.test",
        actor=Actor.system(),
        now=NOW,
    )

    assert outcome.sent is True
    assert outcome.suppressed is None
    assert outcome.external_id == "synthetic-transport-1"
    assert quote.state == QuoteState.SENT.value
    assert len(transport.calls) == 1
    call = transport.calls[0]
    assert call["quote_id"] == quote.id
    assert call["revision_number"] == 1
    assert call["recipient"] == "synthetic@example.test"
    assert call["idempotency_key"] == f"quote:{quote.id}:r1:email"
    assert "Synthetic Travel" in str(call["html"])
    assert "Synthetic terms for tests only." in str(call["html"])
    assert "Margin" not in str(call["html"])

    events = QuoteEventStore(db).timeline(quote.id)
    assert [event.kind for event in events] == [QuoteEventKind.SENT]
    assert events[0].total == outcome.total
    assert events[0].actor_type == "staff"

    row = db.execute(select(AuditLog).where(AuditLog.action == "quote.sent")).scalar_one()
    assert row.after is not None
    assert row.after["external_id"] == "synthetic-transport-1"
    assert row.after["approval_id"] == str(request.approval_id)


def test_delivery_refuses_a_pending_approval(db: Session, tmp_path: Path) -> None:
    quote = make_quote_for(db, None)
    contact = make_contact(db)
    transport = RecordingTransport()
    path = write_presentation_config(tmp_path)
    _svc, request = requested(db, path, quote=quote, contact=contact)

    live = service(db, path, mode=Mode.LIVE, transport=transport)
    with pytest.raises(QuoteSendError, match="approval_pending"):
        live.deliver(
            quote.id,
            approval_id=request.approval_id,
            channel=CHANNEL,
            contact_id=contact.id,
            purpose=PURPOSE,
            recipient="synthetic@example.test",
            actor=Actor.system(),
            now=NOW,
        )

    assert transport.calls == []
    assert quote.state == QuoteState.PENDING_APPROVAL.value


def test_delivery_refuses_without_an_approval_id(db: Session, tmp_path: Path) -> None:
    quote = make_quote_for(db, None)
    contact = make_contact(db)
    path = write_presentation_config(tmp_path)
    svc, _ = requested(db, path, quote=quote, contact=contact)

    with pytest.raises(QuoteSendError, match="approval_required"):
        svc.deliver(
            quote.id,
            approval_id=None,
            channel=CHANNEL,
            contact_id=contact.id,
            purpose=PURPOSE,
            recipient="synthetic@example.test",
            actor=Actor.system(),
            now=NOW,
        )


def test_delivery_refuses_an_approval_for_another_quote(db: Session, tmp_path: Path) -> None:
    first = make_quote_for(db, None)
    second = make_quote_for(db, None)
    contact = make_contact(db)
    path = write_presentation_config(tmp_path)
    _, request = requested(db, path, quote=first, contact=contact)
    approve(db, request.approval_id)
    svc, _ = requested(db, path, quote=second, contact=contact)

    with pytest.raises(QuoteSendError, match="approval_mismatch"):
        svc.deliver(
            second.id,
            approval_id=request.approval_id,
            channel=CHANNEL,
            contact_id=contact.id,
            purpose=PURPOSE,
            recipient="synthetic@example.test",
            actor=Actor.system(),
            now=NOW,
        )


def test_delivery_refuses_after_consent_is_withdrawn(db: Session, tmp_path: Path) -> None:
    quote = make_quote_for(db, None)
    contact = make_contact(db)
    path = write_presentation_config(tmp_path)
    svc, request = requested(db, path, quote=quote, contact=contact)
    approve(db, request.approval_id)
    consent = db.execute(select(Consent).where(Consent.contact_id == contact.id)).scalar_one()
    ConsentService().withdraw(
        db,
        consent_id=consent.id,
        actor=Actor.system(),
        now=NOW,
    )

    with pytest.raises(QuoteSendError, match="no_consent"):
        svc.deliver(
            quote.id,
            approval_id=request.approval_id,
            channel=CHANNEL,
            contact_id=contact.id,
            purpose=PURPOSE,
            recipient="synthetic@example.test",
            actor=Actor.system(),
            now=NOW,
        )


def test_delivery_refuses_an_expired_quote(db: Session, tmp_path: Path) -> None:
    quote = make_quote_for(db, None)
    contact = make_contact(db)
    svc, request = requested(db, write_presentation_config(tmp_path), quote=quote, contact=contact)
    approve(db, request.approval_id)

    with pytest.raises(QuoteSendError, match="expired"):
        svc.deliver(
            quote.id,
            approval_id=request.approval_id,
            channel=CHANNEL,
            contact_id=contact.id,
            purpose=PURPOSE,
            recipient="synthetic@example.test",
            actor=Actor.system(),
            now=quote.expires_at + timedelta(minutes=1),
        )


def test_live_delivery_without_a_transport_refuses(db: Session, tmp_path: Path) -> None:
    quote = make_quote_for(db, None)
    contact = make_contact(db)
    path = write_presentation_config(tmp_path)
    _svc, request = requested(db, path, quote=quote, contact=contact)
    approve(db, request.approval_id)
    live = service(db, path, mode=Mode.LIVE)

    with pytest.raises(QuoteSendError, match="transport_not_configured"):
        live.deliver(
            quote.id,
            approval_id=request.approval_id,
            channel=CHANNEL,
            contact_id=contact.id,
            purpose=PURPOSE,
            recipient="synthetic@example.test",
            actor=Actor.system(),
            now=NOW,
        )


def test_delivery_refuses_an_empty_recipient(db: Session, tmp_path: Path) -> None:
    quote = make_quote_for(db, None)
    contact = make_contact(db)
    path = write_presentation_config(tmp_path)
    svc, request = requested(db, path, quote=quote, contact=contact)
    approve(db, request.approval_id)

    with pytest.raises(QuoteValidationError, match="recipient"):
        svc.deliver(
            quote.id,
            approval_id=request.approval_id,
            channel=CHANNEL,
            contact_id=contact.id,
            purpose=PURPOSE,
            recipient="   ",
            actor=Actor.system(),
            now=NOW,
        )
