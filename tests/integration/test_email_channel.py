"""Integration tests for the email channel adapter.

Tests run against a real PostgreSQL database (pgserver) and verify:
- Inbound email deduplication and idempotency
- Raw email ingestion with RFC 2822 parsing
- Outbound mode/kill-switch/consent/approval gates with real CRM records
- Encrypted storage and privacy-safe audit
"""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

import pytest
from sqlalchemy import text

from travelos.channels.adapters.email import (
    EmailChannelAdapter,
    EmailSendBlockedError,
)
from travelos.channels.service import InboundMessage
from travelos.core.audit import Actor
from travelos.core.config import Mode
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.crm.consent import ConsentService
from travelos.crm.service import CRMService

pytestmark = pytest.mark.integration


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class RecordingTransport:
    """Records delivery calls for test assertions."""

    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    def deliver(
        self,
        *,
        to: str,
        subject: str,
        body: str,
        message_id: str,
        from_address: str | None = None,
    ) -> None:
        self.calls.append(
            {
                "to": to,
                "subject": subject,
                "body": body,
                "message_id": message_id,
                "from_address": from_address or "",
            }
        )


def _make_email_event(
    external_id: str = "email-msg-1",
    thread_id: str = "email-thread-1",
    body: str = "synthetic email body",
    occurred_at: datetime | None = None,
) -> InboundMessage:
    return InboundMessage(
        channel="email",
        external_id=external_id,
        external_thread_id=thread_id,
        body=body,
        occurred_at=occurred_at or datetime.now(UTC),
    )


# ---------------------------------------------------------------------------
# Inbound tests
# ---------------------------------------------------------------------------


def test_email_inbound_deduplicates(db_factory) -> None:  # type: ignore[no-untyped-def]
    tenant = UUID(int=601)
    adapter = EmailChannelAdapter()
    with db_factory() as db:
        first = adapter.receive(
            db,
            event=_make_email_event(external_id="dedup-1"),
            tenant_id=tenant,
        )
        replay = adapter.receive(
            db,
            event=_make_email_event(external_id="dedup-1"),
            tenant_id=tenant,
        )
        assert first.message.id == replay.message.id
        assert first.duplicate is False
        assert replay.duplicate is True
        db.commit()


def test_email_inbound_raw_rfc2822(db_factory) -> None:  # type: ignore[no-untyped-def]
    tenant = UUID(int=602)
    adapter = EmailChannelAdapter()
    raw = (
        "From: sender@example.test\r\n"
        "To: agency@example.test\r\n"
        "Subject: Raw Test\r\n"
        "Message-ID: <raw-rfc-1@example.test>\r\n"
        "Date: Sun, 25 Oct 2026 12:00:00 +0000\r\n"
        "\r\n"
        "Parsed directly from raw message bytes.\r\n"
    )
    with db_factory() as db:
        result = adapter.receive_raw_email(db, raw_email=raw, tenant_id=tenant)
        db.commit()
        assert result.message.external_id == "raw-rfc-1@example.test"
        assert result.message.body == "Parsed directly from raw message bytes."


def test_email_inbound_body_is_encrypted_at_rest(db_factory) -> None:  # type: ignore[no-untyped-def]
    tenant = UUID(int=603)
    adapter = EmailChannelAdapter()
    with db_factory() as db:
        result = adapter.receive(
            db,
            event=_make_email_event(body="private synthetic email content"),
            tenant_id=tenant,
        )
        db.commit()

        stored = db.execute(
            text("SELECT body FROM messages WHERE id = :message_id"),
            {"message_id": result.message.id},
        ).scalar_one()
        audit = db.execute(
            text("SELECT after FROM audit_log WHERE action = 'channel.inbound_received'")
        ).scalar_one()

    assert stored.startswith("enc:v1:")
    assert "private synthetic email content" not in str(audit)


def test_email_inbound_with_crm_link(db_factory) -> None:  # type: ignore[no-untyped-def]
    tenant = UUID(int=604)
    adapter = EmailChannelAdapter()
    crm = CRMService()
    with db_factory() as db:
        customer = crm.create_customer(
            db,
            customer_type="individual",
            display_name="Email Test Customer",
            segment=None,
            actor=Actor.system(),
            tenant_id=tenant,
        )
        contact = crm.create_contact(
            db,
            customer_id=customer.id,
            channel="email",
            value="test-customer@example.test",
            verified=True,
            is_primary=True,
            language="en",
            actor=Actor.system(),
            tenant_id=tenant,
        )
        result = adapter.receive(
            db,
            event=_make_email_event(external_id="crm-linked-1"),
            tenant_id=tenant,
            customer_id=customer.id,
            contact_id=contact.id,
        )
        assert result.conversation.customer_id == customer.id
        assert result.conversation.contact_id == contact.id
        db.commit()


# ---------------------------------------------------------------------------
# Outbound gate tests (with real DB)
# ---------------------------------------------------------------------------


def test_email_send_blocked_in_test_mode(db_factory) -> None:  # type: ignore[no-untyped-def]
    transport = RecordingTransport()
    adapter = EmailChannelAdapter(
        transport=transport,
        mode=Mode.TEST,
        kill_switch_enabled=True,
    )
    with db_factory() as db:
        result = adapter.receive(
            db,
            event=_make_email_event(external_id="send-test-1"),
            tenant_id=DEFAULT_TENANT_ID,
        )
        db.commit()
        with pytest.raises(EmailSendBlockedError, match="test mode"):
            adapter.send(
                result.message,
                db=db,
                to="recipient@example.test",
                subject="Blocked",
            )
    assert transport.calls == []


def test_email_send_blocked_in_shadow_mode(db_factory) -> None:  # type: ignore[no-untyped-def]
    transport = RecordingTransport()
    adapter = EmailChannelAdapter(
        transport=transport,
        mode=Mode.SHADOW,
        kill_switch_enabled=True,
    )
    with db_factory() as db:
        result = adapter.receive(
            db,
            event=_make_email_event(external_id="send-shadow-1"),
            tenant_id=DEFAULT_TENANT_ID,
        )
        db.commit()
        with pytest.raises(EmailSendBlockedError, match="shadow mode"):
            adapter.send(
                result.message,
                db=db,
                to="recipient@example.test",
                subject="Blocked",
            )
    assert transport.calls == []


def test_email_send_blocked_without_consent(db_factory) -> None:  # type: ignore[no-untyped-def]
    tenant = UUID(int=605)
    crm = CRMService()
    consent_svc = ConsentService()
    transport = RecordingTransport()
    adapter = EmailChannelAdapter(
        transport=transport,
        mode=Mode.LIVE,
        kill_switch_enabled=True,
        consent_checker=consent_svc,
    )
    with db_factory() as db:
        customer = crm.create_customer(
            db,
            customer_type="individual",
            display_name="No Consent Customer",
            segment=None,
            actor=Actor.system(),
            tenant_id=tenant,
        )
        contact = crm.create_contact(
            db,
            customer_id=customer.id,
            channel="email",
            value="no-consent@example.test",
            verified=True,
            is_primary=True,
            language="en",
            actor=Actor.system(),
            tenant_id=tenant,
        )
        result = adapter.receive(
            db,
            event=_make_email_event(external_id="consent-deny-1"),
            tenant_id=tenant,
            customer_id=customer.id,
            contact_id=contact.id,
        )
        db.commit()
        with pytest.raises(EmailSendBlockedError, match="consent"):
            adapter.send(
                result.message,
                db=db,
                to="no-consent@example.test",
                subject="Should Not Send",
                contact_id=contact.id,
                tenant_id=tenant,
            )
    assert transport.calls == []


def test_email_send_with_consent_in_live_mode(db_factory) -> None:  # type: ignore[no-untyped-def]
    tenant = UUID(int=606)
    crm = CRMService()
    consent_svc = ConsentService()
    transport = RecordingTransport()
    adapter = EmailChannelAdapter(
        transport=transport,
        mode=Mode.LIVE,
        kill_switch_enabled=True,
        consent_checker=consent_svc,
    )
    with db_factory() as db:
        customer = crm.create_customer(
            db,
            customer_type="individual",
            display_name="Consented Customer",
            segment=None,
            actor=Actor.system(),
            tenant_id=tenant,
        )
        contact = crm.create_contact(
            db,
            customer_id=customer.id,
            channel="email",
            value="consented@example.test",
            verified=True,
            is_primary=True,
            language="en",
            actor=Actor.system(),
            tenant_id=tenant,
        )
        consent_svc.grant(
            db,
            contact_id=contact.id,
            channel="email",
            purpose="transactional",
            source="synthetic test",
            proof_ref=None,
            actor=Actor.system(),
            tenant_id=tenant,
        )
        result = adapter.receive(
            db,
            event=_make_email_event(external_id="consent-ok-1"),
            tenant_id=tenant,
            customer_id=customer.id,
            contact_id=contact.id,
        )
        db.commit()
        adapter.send(
            result.message,
            db=db,
            to="consented@example.test",
            subject="Welcome",
            contact_id=contact.id,
            tenant_id=tenant,
        )
        assert result.message.status == "sent"
    assert len(transport.calls) == 1
    assert transport.calls[0]["to"] == "consented@example.test"
    assert transport.calls[0]["subject"] == "Welcome"
