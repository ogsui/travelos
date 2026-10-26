"""WhatsApp Business Cloud API channel adapter.

Inbound: verifies Meta webhook signatures, extracts text/media/button messages,
deduplicates by message ID (wamid), normalizes conversations by phone number,
and stores encrypted at rest via ChannelService.

Outbound: enforces mode gates (blocked in test and shadow modes), channel kill switch,
recipient consent and suppression, approval policies, and the Meta 24-hour customer
care window (messages outside 24 hours of customer inbound require approved templates).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from uuid import UUID

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.approval.errors import UnknownActionError
from travelos.approval.matrix import AutonomyLevel, get_spec
from travelos.approval.models import ApprovalRequest
from travelos.approval.service import ApprovalService
from travelos.channels.models import Conversation, Message
from travelos.channels.service import ChannelService, InboundMessage, InboundResult
from travelos.core.audit import Actor, AuditWriter
from travelos.core.config import Mode
from travelos.core.db import DEFAULT_TENANT_ID

logger = logging.getLogger("travelos.channels.whatsapp")

SERVICE_WINDOW_HOURS = 24
# Placeholder catalogue of the transactional templates the agency intends to use.
# TODO(owner): confirm the exact names, language codes and categories approved in
# Meta Business Manager, then replace this set before any live WhatsApp send.
APPROVED_TEMPLATES: frozenset[str] = frozenset(
    {
        "instant_acknowledgement",
        "quote_ready",
        "payment_reminder",
        "booking_confirmed",
        "travel_documents",
        "disruption_alert",
    }
)


# ---------------------------------------------------------------------------
# Typed errors
# ---------------------------------------------------------------------------


class WhatsAppSendBlockedError(RuntimeError):
    """WhatsApp send was blocked by a safety gate or policy check."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class WhatsAppPayloadError(ValueError):
    """Webhook payload is malformed or missing required fields."""


# ---------------------------------------------------------------------------
# Signature verification and challenge handshake
# ---------------------------------------------------------------------------


def verify_meta_signature(
    raw_body: bytes,
    signature_header: str | None,
    app_secret: str,
) -> bool:
    """Verify Meta's X-Hub-Signature-256 header using SHA-256 HMAC."""
    if not signature_header or not app_secret:
        return False
    if not signature_header.startswith("sha256="):
        return False
    expected = (
        "sha256=" + hmac.new(app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
    )
    return hmac.compare_digest(expected, signature_header)


def verify_webhook_challenge(
    *,
    mode: str | None,
    token: str | None,
    challenge: str | None,
    expected_token: str,
) -> str | None:
    """Handle Meta's GET verification handshake for webhook setup."""
    if mode == "subscribe" and token and hmac.compare_digest(token, expected_token):
        return challenge
    return None


# ---------------------------------------------------------------------------
# 24-hour window and templates
# ---------------------------------------------------------------------------


def is_within_24h_window(
    last_inbound_at: datetime | None,
    now: datetime | None = None,
) -> bool:
    """Check whether a conversation is within the Meta 24h customer care window."""
    if last_inbound_at is None:
        return False
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=UTC)
    if last_inbound_at.tzinfo is None:
        last_inbound_at = last_inbound_at.replace(tzinfo=UTC)
    return timedelta(0) <= (current_time - last_inbound_at) <= timedelta(hours=SERVICE_WINDOW_HOURS)


def is_approved_template(template_id: str) -> bool:
    """Check if a template name is in the approved template catalogue."""
    return template_id.strip().lower() in APPROVED_TEMPLATES


# ---------------------------------------------------------------------------
# Cloud API Webhook Parser
# ---------------------------------------------------------------------------


def _as_mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _as_sequence(value: object) -> list[Any]:
    return value if isinstance(value, list) else []


def _text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _decode_payload(payload: dict[str, Any] | bytes | str) -> dict[str, Any]:
    if isinstance(payload, dict):
        return payload
    if not isinstance(payload, bytes | str):
        raise WhatsAppPayloadError("payload must be a JSON object")
    try:
        decoded = (
            json.loads(payload.decode("utf-8"))
            if isinstance(payload, bytes)
            else json.loads(payload)
        )
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise WhatsAppPayloadError("payload is not valid JSON") from exc
    if not isinstance(decoded, dict):
        raise WhatsAppPayloadError("payload must be a JSON object")
    return decoded


def parse_whatsapp_webhook(
    payload: dict[str, Any] | bytes | str,
) -> list[InboundMessage]:
    """Parse official Meta WhatsApp Business Cloud API webhook payload.

    Extracts text, image, document, video, audio, interactive/button responses,
    and maps them to normalized InboundMessage events. Delivery-status batches
    contain no ``messages`` array and yield an empty list. Unknown or malformed
    shapes are skipped rather than raising, so one bad entry cannot lose the
    messages in a batch Meta retries.
    """
    data = _decode_payload(payload)

    messages: list[InboundMessage] = []
    for entry in _as_sequence(data.get("entry")):
        for change in _as_sequence(_as_mapping(entry).get("changes")):
            val = _as_mapping(_as_mapping(change).get("value"))
            if val.get("messaging_product") != "whatsapp":
                continue

            for msg in _as_sequence(val.get("messages")):
                message = _as_mapping(msg)
                msg_id = _text(message.get("id"))
                from_num = _text(message.get("from"))
                if not msg_id or not from_num:
                    continue

                occurred_at = datetime.now(UTC)
                raw_ts = message.get("timestamp")
                if isinstance(raw_ts, str | int):
                    try:
                        occurred_at = datetime.fromtimestamp(int(raw_ts), tz=UTC)
                    except (TypeError, ValueError, OSError, OverflowError):
                        occurred_at = datetime.now(UTC)

                msg_type = _text(message.get("type")) or "text"
                body = ""
                media_refs: list[str] = []

                if msg_type == "text":
                    body = _text(_as_mapping(message.get("text")).get("body"))
                elif msg_type == "image":
                    media = _as_mapping(message.get("image"))
                    media_id = _text(media.get("id"))
                    body = _text(media.get("caption")) or "[Image]"
                    if media_id:
                        media_refs.append(f"whatsapp:image:{media_id}")
                elif msg_type == "document":
                    media = _as_mapping(message.get("document"))
                    media_id = _text(media.get("id"))
                    filename = _text(media.get("filename")) or "document"
                    body = _text(media.get("caption")) or f"[Document: {filename}]"
                    if media_id:
                        media_refs.append(f"whatsapp:document:{media_id}:{filename}")
                elif msg_type in ("audio", "voice"):
                    media = _as_mapping(message.get(msg_type))
                    media_id = _text(media.get("id"))
                    body = "[Voice/Audio message]"
                    if media_id:
                        media_refs.append(f"whatsapp:audio:{media_id}")
                elif msg_type == "video":
                    media = _as_mapping(message.get("video"))
                    media_id = _text(media.get("id"))
                    body = _text(media.get("caption")) or "[Video]"
                    if media_id:
                        media_refs.append(f"whatsapp:video:{media_id}")
                elif msg_type == "interactive":
                    interactive = _as_mapping(message.get("interactive"))
                    itype = _text(interactive.get("type"))
                    if itype == "button_reply":
                        body = _text(_as_mapping(interactive.get("button_reply")).get("title"))
                    elif itype == "list_reply":
                        body = _text(_as_mapping(interactive.get("list_reply")).get("title"))
                    else:
                        body = "[Interactive reply]"
                elif msg_type == "button":
                    body = _text(_as_mapping(message.get("button")).get("text")) or "[Button reply]"
                elif msg_type == "location":
                    loc = _as_mapping(message.get("location"))
                    body = f"[Location: lat={loc.get('latitude')}, lng={loc.get('longitude')}]"
                else:
                    body = f"[{msg_type} message]"

                messages.append(
                    InboundMessage(
                        channel="whatsapp",
                        external_id=msg_id,
                        external_thread_id=from_num,
                        body=body or f"[{msg_type}]",
                        occurred_at=occurred_at,
                        media_refs=tuple(media_refs),
                    )
                )

    return messages


# ---------------------------------------------------------------------------
# Transport layer
# ---------------------------------------------------------------------------


def _digits_only(phone: str) -> str:
    """Normalize a phone number to the digits-only form the Cloud API expects."""
    return "".join(character for character in phone if character in "0123456789")


class WhatsAppTransport(Protocol):
    """Outbound WhatsApp message delivery protocol."""

    def deliver(
        self,
        *,
        to: str,
        body: str,
        message_id: str,
        template_id: str | None = None,
        template_params: Mapping[str, Any] | None = None,
    ) -> None:
        """Deliver message to WhatsApp recipient. Raises on network/API failure."""
        ...


@dataclass
class MockWhatsAppTransport:
    """Mock recording transport for tests and simulation."""

    sent: list[dict[str, Any]] = field(default_factory=list)

    def deliver(
        self,
        *,
        to: str,
        body: str,
        message_id: str,
        template_id: str | None = None,
        template_params: Mapping[str, Any] | None = None,
    ) -> None:
        self.sent.append(
            {
                "to": to,
                "body": body,
                "message_id": message_id,
                "template_id": template_id,
                "template_params": dict(template_params or {}),
            }
        )


class CloudAPIWhatsAppTransport:
    """Official Meta WhatsApp Business Cloud API transport.

    Connects to Meta Graph API at POST /v18.0/{phone_number_id}/messages.
    Requires official owner-supplied credentials.
    """

    def __init__(
        self,
        *,
        phone_number_id: str,
        access_token: str,
        base_url: str = "https://graph.facebook.com/v18.0",
        template_language: str = "en",
        timeout: float = 15.0,
    ) -> None:
        if not phone_number_id.strip() or not access_token.strip():
            raise ValueError(
                "WhatsApp Business Cloud API requires phone_number_id and access_token. "
                "TODO(owner): Provide WABA credentials from Meta Business Manager."
            )
        self._phone_number_id = phone_number_id.strip()
        self._access_token = access_token.strip()
        self._base_url = base_url.rstrip("/")
        self._template_language = template_language.strip() or "en"
        self._timeout = timeout

    def deliver(
        self,
        *,
        to: str,
        body: str,
        message_id: str,
        template_id: str | None = None,
        template_params: Mapping[str, Any] | None = None,
    ) -> None:
        url = f"{self._base_url}/{self._phone_number_id}/messages"
        headers = {
            "Authorization": f"Bearer {self._access_token}",
            "Content-Type": "application/json",
        }

        payload: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": _digits_only(to),
        }

        if template_id:
            payload["type"] = "template"
            payload["template"] = {
                "name": template_id,
                "language": {"code": self._template_language},
            }
            if template_params:
                components = template_params.get("components")
                if components:
                    payload["template"]["components"] = components
        else:
            payload["type"] = "text"
            payload["text"] = {"body": body}

        try:
            with httpx.Client(timeout=self._timeout) as client:
                response = client.post(url, headers=headers, json=payload)
                response.raise_for_status()
        except httpx.HTTPError as exc:
            logger.exception("WhatsApp Cloud API delivery failed for message_id=%s", message_id)
            raise RuntimeError(f"WhatsApp Cloud API delivery error: {exc}") from exc


# ---------------------------------------------------------------------------
# Protocols for dependencies
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


# ---------------------------------------------------------------------------
# Channel Adapter Implementation
# ---------------------------------------------------------------------------


class WhatsAppChannelAdapter:
    """WhatsApp adapter implementing the ChannelAdapter protocol."""

    def __init__(
        self,
        *,
        transport: WhatsAppTransport | None = None,
        mode: Mode = Mode.TEST,
        kill_switch_enabled: bool = False,
        consent_checker: ConsentChecker | None = None,
        approvals: ApprovalService | None = None,
        audit: AuditWriter | None = None,
    ) -> None:
        self._service = ChannelService()
        self._transport = transport or MockWhatsAppTransport()
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
        """Persist an inbound WhatsApp event idempotently."""
        return self._service.receive(
            db,
            event=event,
            tenant_id=tenant_id,
            customer_id=customer_id,
            contact_id=contact_id,
        )

    def receive_webhook(
        self,
        db: Session,
        *,
        payload: dict[str, Any] | bytes | str,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        customer_id: UUID | None = None,
        contact_id: UUID | None = None,
    ) -> list[InboundResult]:
        """Parse raw Meta webhook payload and persist all extracted messages."""
        events = parse_whatsapp_webhook(payload)
        results: list[InboundResult] = []
        for event in events:
            result = self.receive(
                db,
                event=event,
                tenant_id=tenant_id,
                customer_id=customer_id,
                contact_id=contact_id,
            )
            results.append(result)
        return results

    # -- Outbound (send) ----------------------------------------------------

    def send(
        self,
        message: Message,
        *,
        db: Session | None = None,
        to: str | None = None,
        purpose: str = "transactional",
        contact_id: UUID | None = None,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        action_type: str | None = None,
        approval_id: UUID | None = None,
        template_id: str | None = None,
        template_params: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> None:
        """Send a WhatsApp message, enforcing all safety and window gates."""
        current_time = now or datetime.now(UTC)

        # Gate 1: Mode gate
        if self._mode in (Mode.TEST, Mode.SHADOW):
            raise WhatsAppSendBlockedError(
                f"outbound WhatsApp message is blocked in {self._mode.value} mode"
            )

        # Gate 2: Kill switch gate
        if not self._kill_switch_enabled:
            raise WhatsAppSendBlockedError("whatsapp kill switch is disabled")

        # Gate 3: Consent & suppression gate
        if contact_id is not None and self._consent is not None:
            if db is None:
                raise WhatsAppSendBlockedError("db session required for consent check")
            if not self._consent.can_send(
                db,
                contact_id=contact_id,
                channel="whatsapp",
                purpose=purpose,
                tenant_id=tenant_id,
            ):
                raise WhatsAppSendBlockedError("contact does not have consent for whatsapp sends")

        # Gate 4: Approval check
        self._check_approval(
            db,
            action_type=action_type,
            approval_id=approval_id,
            tenant_id=tenant_id,
        )

        # Gate 5: 24-hour service window check
        self._check_service_window(
            db,
            message=message,
            template_id=template_id,
            now=current_time,
            tenant_id=tenant_id,
        )

        # Resolve destination phone number
        recipient = to
        if not recipient and contact_id is not None and db is not None:
            from travelos.crm.models import Contact

            contact = db.execute(
                select(Contact.value).where(
                    Contact.id == contact_id, Contact.tenant_id == tenant_id
                )
            ).scalar_one_or_none()
            recipient = contact

        if not recipient or not _digits_only(recipient):
            raise WhatsAppSendBlockedError("recipient phone number is required")

        # Deliver via transport
        self._transport.deliver(
            to=recipient,
            body=message.body,
            message_id=str(message.id),
            template_id=template_id,
            template_params=template_params,
        )

        # Update message state
        if db is not None:
            message.status = "sent"
            if template_id:
                message.template_id = template_id
            db.flush()
            self._audit.record(
                db,
                action="channel.outbound_sent",
                actor=Actor.system(),
                subject_type="message",
                subject_id=message.id,
                after={
                    "channel": "whatsapp",
                    "purpose": purpose,
                    "action_type": action_type,
                    "template_id": template_id,
                },
            )

    def _check_service_window(
        self,
        db: Session | None,
        *,
        message: Message,
        template_id: str | None,
        now: datetime,
        tenant_id: UUID,
    ) -> None:
        """Enforce the Meta 24-hour customer care window rule."""
        last_inbound_at: datetime | None = None

        if hasattr(message, "conversation") and message.conversation is not None:
            last_inbound_at = message.conversation.last_inbound_at
        elif db is not None and message.conversation_id is not None:
            conv = db.execute(
                select(Conversation.last_inbound_at).where(
                    Conversation.id == message.conversation_id,
                    Conversation.tenant_id == tenant_id,
                )
            ).scalar_one_or_none()
            last_inbound_at = conv

        inside_window = is_within_24h_window(last_inbound_at, now=now)

        if template_id is not None and not is_approved_template(template_id):
            raise WhatsAppSendBlockedError(f"template '{template_id}' is not an approved template")
        if not inside_window and template_id is None:
            raise WhatsAppSendBlockedError(
                "outbound message outside 24-hour window requires an approved template"
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
                raise WhatsAppSendBlockedError("db session required for approval check")
            req = db.execute(
                select(ApprovalRequest).where(
                    ApprovalRequest.id == approval_id,
                    ApprovalRequest.tenant_id == tenant_id,
                )
            ).scalar_one_or_none()
            if req is None or req.status != "approved":
                raise WhatsAppSendBlockedError("approval request has not been approved")
            return

        if action_type is not None:
            try:
                spec = get_spec(action_type)
                if spec.default_level in (AutonomyLevel.A0, AutonomyLevel.A1):
                    raise WhatsAppSendBlockedError(
                        f"approval is required for action: {action_type}"
                    )
            except UnknownActionError:
                raise WhatsAppSendBlockedError(f"unknown action type: {action_type}") from None

    # -- Status / templates -------------------------------------------------

    @staticmethod
    def status(_external_id: str) -> str:
        """Delivery status hook. TODO(owner): wire to Meta delivery webhooks."""
        return "unavailable"

    @staticmethod
    def templates() -> tuple[str, ...]:
        """Return registered approved templates."""
        return tuple(sorted(APPROVED_TEMPLATES))

    def timeline(self, db: Session, **filters: object) -> list[Message]:
        return self._service.timeline(db, **filters)  # type: ignore[arg-type]
