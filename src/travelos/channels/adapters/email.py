"""Email channel adapter.

Inbound: delegates to ChannelService (idempotent, encrypted at rest, threading support).
Outbound: enforces mode gates, kill switch, consent/suppression, and approval checks
before delivering via an EmailTransport. In test and shadow modes, outbound sending
is blocked (AGENTS.md).

Development uses Mailpit; production SMTP/SES credentials are supplied by the owner.
"""

from __future__ import annotations

import email
import email.message
import email.policy
import hashlib
import logging
import smtplib
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.approval.models import ApprovalRequest
from travelos.approval.service import ApprovalService
from travelos.channels.models import Message
from travelos.channels.service import ChannelService, InboundMessage, InboundResult
from travelos.core.audit import Actor, AuditWriter
from travelos.core.config import Mode
from travelos.core.db import DEFAULT_TENANT_ID

logger = logging.getLogger("travelos.channels.email")


# ---------------------------------------------------------------------------
# Transport layer
# ---------------------------------------------------------------------------


class EmailTransport(Protocol):
    """Pluggable email delivery backend."""

    def deliver(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        message_id: str,
        from_address: str | None = None,
    ) -> None:
        """Send an email. Raises on transport failure."""
        ...


@dataclass(frozen=True, slots=True)
class SMTPTransportConfig:
    host: str
    port: int
    username: str | None = None
    password: str | None = None
    use_tls: bool = False
    use_starttls: bool = False
    from_address: str = "noreply@travelos.local"


class SMTPTransport:
    """Sends email via SMTP (Mailpit in dev, authenticated relay in live)."""

    def __init__(self, config: SMTPTransportConfig) -> None:
        self._config = config

    def deliver(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        message_id: str,
        from_address: str | None = None,
    ) -> None:
        cfg = self._config
        msg = email.message.EmailMessage()
        msg["To"] = to
        msg["Subject"] = subject
        msg["Message-ID"] = f"<{message_id}@travelos.local>"
        msg["From"] = from_address or cfg.from_address
        msg.set_content(body)
        try:
            server: smtplib.SMTP
            if cfg.use_tls:
                server = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=15)
            else:
                server = smtplib.SMTP(cfg.host, cfg.port, timeout=15)
            try:
                if cfg.use_starttls:
                    server.starttls()
                if cfg.username and cfg.password:
                    server.login(cfg.username, cfg.password)
                server.send_message(msg)
            finally:
                server.quit()
        except smtplib.SMTPException:
            logger.exception("SMTP delivery failed for message_id=%s", message_id)
            raise


class MailpitTransport(SMTPTransport):
    """Convenience transport for local Mailpit development."""

    def __init__(
        self,
        host: str = "localhost",
        port: int = 1025,
        from_address: str = "travelos@example.test",
    ) -> None:
        super().__init__(
            SMTPTransportConfig(
                host=host,
                port=port,
                from_address=from_address,
            )
        )


class NullTransport:
    """Transport that silently discards messages for testing."""

    def deliver(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        message_id: str,
        from_address: str | None = None,
    ) -> None:
        logger.debug("NullTransport: discarded email to=%s message_id=%s", to, message_id)


# ---------------------------------------------------------------------------
# Send gate errors
# ---------------------------------------------------------------------------


class EmailSendBlockedError(RuntimeError):
    """Email send was blocked by a mode gate, kill switch, consent, or approval check."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


# ---------------------------------------------------------------------------
# Protocols
# ---------------------------------------------------------------------------


class ConsentChecker(Protocol):
    """Check whether we may send to a contact on a channel for a purpose."""

    def can_send(
        self,
        db: Session,
        *,
        contact_id: UUID,
        channel: str,
        purpose: str,
        tenant_id: UUID = DEFAULT_TENANT_ID,
    ) -> bool: ...


class ApprovalChecker(Protocol):
    """Verify approval state for an action."""

    def is_approved(
        self,
        db: Session,
        *,
        action_type: str,
        approval_id: UUID | None = None,
        tenant_id: UUID = DEFAULT_TENANT_ID,
    ) -> bool: ...


# ---------------------------------------------------------------------------
# Raw email parser
# ---------------------------------------------------------------------------


def parse_raw_email(raw: bytes | str) -> InboundMessage:
    """Parse RFC 2822 raw email into a normalized InboundMessage."""
    if isinstance(raw, str):
        msg = email.message_from_string(raw, policy=email.policy.default)
    else:
        msg = email.message_from_bytes(raw, policy=email.policy.default)

    raw_id = msg.get("Message-ID", "").strip("<> ").strip()
    if not raw_id:
        body_digest = hashlib.sha256(
            raw.encode("utf-8") if isinstance(raw, str) else raw
        ).hexdigest()[:24]
        raw_id = f"email-unidentified-{body_digest}"

    in_reply_to = msg.get("In-Reply-To", "").strip()
    references = msg.get("References", "").strip()
    if in_reply_to:
        thread_id = in_reply_to.split()[-1].strip("<> ")
    elif references:
        thread_id = references.split()[0].strip("<> ")
    else:
        thread_id = raw_id

    date_header = msg.get("Date")
    if date_header:
        try:
            occurred_at = parsedate_to_datetime(date_header)
            if occurred_at.tzinfo is None:
                occurred_at = occurred_at.replace(tzinfo=UTC)
            else:
                occurred_at = occurred_at.astimezone(UTC)
        except Exception:
            occurred_at = datetime.now(UTC)
    else:
        occurred_at = datetime.now(UTC)

    body_content: object = ""
    media_refs: list[str] = []
    if msg.is_multipart():
        for part in msg.walk():
            content_type = part.get_content_type()
            disposition = str(part.get_content_disposition() or "")
            if disposition == "attachment":
                filename = part.get_filename() or "attachment"
                media_refs.append(f"attachment:{filename}")
            elif content_type in ("text/plain", "text/html") and not body_content:
                body_content = part.get_content()
    else:
        body_content = msg.get_content() if hasattr(msg, "get_content") else msg.get_payload()

    body = str(body_content).strip() if body_content is not None else ""

    return InboundMessage(
        channel="email",
        external_id=raw_id,
        external_thread_id=thread_id,
        body=body,
        occurred_at=occurred_at,
        media_refs=tuple(media_refs),
    )


# ---------------------------------------------------------------------------
# Adapter implementation
# ---------------------------------------------------------------------------


class EmailChannelAdapter:
    """Email adapter implementing the ChannelAdapter protocol."""

    def __init__(
        self,
        *,
        transport: EmailTransport | None = None,
        mode: Mode = Mode.TEST,
        kill_switch_enabled: bool = False,
        consent_checker: ConsentChecker | None = None,
        approvals: ApprovalService | None = None,
        audit: AuditWriter | None = None,
    ) -> None:
        self._service = ChannelService()
        self._transport = transport or NullTransport()
        self._mode = mode
        self._kill_switch_enabled = kill_switch_enabled
        self._consent = consent_checker
        self._approvals = approvals
        self._audit = audit or AuditWriter()

    # -- Inbound (receive) --------------------------------------------------

    def receive(
        self,
        db: Session,
        *,
        event: InboundMessage,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        customer_id: UUID | None = None,
        contact_id: UUID | None = None,
    ) -> InboundResult:
        """Persist an inbound email event idempotently."""
        return self._service.receive(
            db,
            event=event,
            tenant_id=tenant_id,
            customer_id=customer_id,
            contact_id=contact_id,
        )

    def receive_raw_email(
        self,
        db: Session,
        *,
        raw_email: bytes | str,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        customer_id: UUID | None = None,
        contact_id: UUID | None = None,
    ) -> InboundResult:
        """Parse raw RFC 2822 email and persist it."""
        event = parse_raw_email(raw_email)
        return self.receive(
            db,
            event=event,
            tenant_id=tenant_id,
            customer_id=customer_id,
            contact_id=contact_id,
        )

    # -- Outbound (send) ----------------------------------------------------

    def send(
        self,
        message: Message,
        *,
        db: Session | None = None,
        to: str | None = None,
        subject: str = "",
        purpose: str = "transactional",
        contact_id: UUID | None = None,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        action_type: str | None = None,
        approval_id: UUID | None = None,
        from_address: str | None = None,
    ) -> None:
        """Send an email, enforcing all safety gates."""
        # Gate 1: Mode gate
        if self._mode in (Mode.TEST, Mode.SHADOW):
            raise EmailSendBlockedError(f"outbound email is blocked in {self._mode.value} mode")

        # Gate 2: Kill switch gate
        if not self._kill_switch_enabled:
            raise EmailSendBlockedError("email kill switch is disabled")

        # Gate 3: Consent & suppression gate
        if contact_id is not None and self._consent is not None:
            if db is None:
                raise EmailSendBlockedError("db session required for consent check")
            if not self._consent.can_send(
                db,
                contact_id=contact_id,
                channel="email",
                purpose=purpose,
                tenant_id=tenant_id,
            ):
                raise EmailSendBlockedError("contact does not have consent for email sends")

        # Gate 4: Approval check
        self._check_approval(
            db,
            action_type=action_type,
            approval_id=approval_id,
            tenant_id=tenant_id,
        )

        recipient = to
        if not recipient and contact_id is not None and db is not None:
            from travelos.crm.models import Contact

            contact = db.execute(
                select(Contact.value).where(
                    Contact.id == contact_id, Contact.tenant_id == tenant_id
                )
            ).scalar_one_or_none()
            recipient = contact

        if not recipient:
            raise EmailSendBlockedError("recipient email address is required")

        self._transport.deliver(
            to=recipient,
            subject=subject,
            body=message.body,
            message_id=str(message.id),
            from_address=from_address,
        )

        if db is not None:
            message.status = "sent"
            db.flush()
            self._audit.record(
                db,
                action="channel.outbound_sent",
                actor=Actor.system(),
                subject_type="message",
                subject_id=message.id,
                after={
                    "channel": "email",
                    "purpose": purpose,
                    "action_type": action_type,
                },
            )

    def _check_approval(
        self,
        db: Session | None,
        *,
        action_type: str | None,
        approval_id: UUID | None,
        tenant_id: UUID,
    ) -> None:
        """Enforce approval requirements based on action_type and approval_id."""
        if approval_id is not None:
            if db is None:
                raise EmailSendBlockedError("db session required for approval check")
            req = db.execute(
                select(ApprovalRequest).where(
                    ApprovalRequest.id == approval_id,
                    ApprovalRequest.tenant_id == tenant_id,
                )
            ).scalar_one_or_none()
            if req is None or req.status != "approved":
                raise EmailSendBlockedError("approval request has not been approved")
            return

        if action_type is not None:
            from travelos.approval.errors import UnknownActionError
            from travelos.approval.matrix import AutonomyLevel, get_spec

            try:
                spec = get_spec(action_type)
                if spec.default_level in (AutonomyLevel.A0, AutonomyLevel.A1):
                    raise EmailSendBlockedError(f"approval is required for action: {action_type}")
            except UnknownActionError:
                raise EmailSendBlockedError(f"unknown action type: {action_type}") from None

    # -- Status / templates -------------------------------------------------

    @staticmethod
    def status(_external_id: str) -> str:
        """Delivery status hook. TODO(owner): wire to provider webhook/event."""
        return "unavailable"

    @staticmethod
    def templates() -> tuple[str, ...]:
        return ()

    def timeline(self, db: Session, **filters: object) -> list[Message]:
        return self._service.timeline(db, **filters)  # type: ignore[arg-type]
