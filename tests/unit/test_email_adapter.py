"""Unit tests for the email channel adapter: mode gates, kill switch, consent, approval, and MIME parser."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest

from travelos.channels.adapters.email import (
    EmailChannelAdapter,
    EmailSendBlockedError,
    NullTransport,
    SMTPTransportConfig,
    parse_raw_email,
)
from travelos.core.config import Mode


class FakeMessage:
    """Minimal stand-in for a Message ORM instance."""

    def __init__(self, *, body: str = "synthetic body", msg_id: UUID | None = None) -> None:
        self.id = msg_id or uuid4()
        self.body = body
        self.channel = "email"
        self.direction = "outbound"
        self.status = "received"


class RecordingTransport:
    """Records delivery calls."""

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


class DenyAllConsent:
    def can_send(self, _db: object, **_kw: object) -> bool:
        return False


class AllowAllConsent:
    def can_send(self, _db: object, **_kw: object) -> bool:
        return True


class DummySession:
    def flush(self) -> None:
        pass

    def add(self, _obj: object) -> None:
        pass


class DummyAuditWriter:
    def record(self, *args: object, **kwargs: object) -> None:
        pass


# ---------------------------------------------------------------------------
# Mode gate tests
# ---------------------------------------------------------------------------


def test_send_blocked_in_test_mode() -> None:
    adapter = EmailChannelAdapter(mode=Mode.TEST, kill_switch_enabled=True)
    msg = FakeMessage()
    with pytest.raises(EmailSendBlockedError, match="test mode"):
        adapter.send(msg, to="x@example.test", subject="hi")  # type: ignore[arg-type]


def test_send_blocked_in_shadow_mode() -> None:
    adapter = EmailChannelAdapter(mode=Mode.SHADOW, kill_switch_enabled=True)
    msg = FakeMessage()
    with pytest.raises(EmailSendBlockedError, match="shadow mode"):
        adapter.send(msg, to="x@example.test", subject="hi")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Kill switch tests
# ---------------------------------------------------------------------------


def test_send_blocked_when_kill_switch_disabled() -> None:
    adapter = EmailChannelAdapter(mode=Mode.LIVE, kill_switch_enabled=False)
    msg = FakeMessage()
    with pytest.raises(EmailSendBlockedError, match="kill switch"):
        adapter.send(msg, to="x@example.test", subject="hi")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Consent tests
# ---------------------------------------------------------------------------


def test_send_blocked_without_consent() -> None:
    adapter = EmailChannelAdapter(
        mode=Mode.LIVE,
        kill_switch_enabled=True,
        consent_checker=DenyAllConsent(),  # type: ignore[arg-type]
    )
    msg = FakeMessage()
    with pytest.raises(EmailSendBlockedError, match="consent"):
        adapter.send(
            msg,  # type: ignore[arg-type]
            db=object(),  # type: ignore[arg-type]
            to="x@example.test",
            subject="hi",
            contact_id=uuid4(),
        )


def test_send_succeeds_with_consent_in_live_mode() -> None:
    transport = RecordingTransport()
    adapter = EmailChannelAdapter(
        transport=transport,
        mode=Mode.LIVE,
        kill_switch_enabled=True,
        consent_checker=AllowAllConsent(),  # type: ignore[arg-type]
        audit=DummyAuditWriter(),  # type: ignore[arg-type]
    )
    msg = FakeMessage(body="synthetic test body")
    adapter.send(
        msg,  # type: ignore[arg-type]
        db=DummySession(),  # type: ignore[arg-type]
        to="casey@example.test",
        subject="Test Subject",
        contact_id=uuid4(),
    )
    assert len(transport.calls) == 1
    assert transport.calls[0]["to"] == "casey@example.test"
    assert transport.calls[0]["subject"] == "Test Subject"
    assert transport.calls[0]["body"] == "synthetic test body"


# ---------------------------------------------------------------------------
# Approval gate tests
# ---------------------------------------------------------------------------


def test_send_blocked_when_action_requires_approval() -> None:
    adapter = EmailChannelAdapter(
        mode=Mode.LIVE,
        kill_switch_enabled=True,
    )
    msg = FakeMessage()
    with pytest.raises(EmailSendBlockedError, match="approval is required for action"):
        adapter.send(
            msg,  # type: ignore[arg-type]
            db=DummySession(),  # type: ignore[arg-type]
            to="x@example.test",
            subject="Reply",
            action_type="message.sales_reply",
        )


# ---------------------------------------------------------------------------
# MIME parsing tests
# ---------------------------------------------------------------------------


def test_parse_raw_email_singlepart() -> None:
    raw = (
        "From: sender@example.test\r\n"
        "To: agency@example.test\r\n"
        "Subject: Inquiry for Goa trip\r\n"
        "Message-ID: <msg-12345@example.test>\r\n"
        "Date: Mon, 25 Oct 2026 10:00:00 +0530\r\n"
        "\r\n"
        "Hi, I would like to book a trip to Goa for 4 people.\r\n"
    )
    inbound = parse_raw_email(raw)
    assert inbound.channel == "email"
    assert inbound.external_id == "msg-12345@example.test"
    assert inbound.external_thread_id == "msg-12345@example.test"
    assert "Inquiry for Goa trip" not in inbound.body
    assert "Hi, I would like to book a trip to Goa" in inbound.body
    assert inbound.occurred_at.tzinfo is not None


def test_parse_raw_email_multipart_with_reply() -> None:
    raw = (
        "From: sender@example.test\r\n"
        "To: agency@example.test\r\n"
        "Subject: Re: Quote #101\r\n"
        "Message-ID: <msg-reply-99@example.test>\r\n"
        "In-Reply-To: <original-thread-42@example.test>\r\n"
        "References: <original-thread-42@example.test>\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/mixed; boundary="BOUNDARY"\r\n'
        "\r\n"
        "--BOUNDARY\r\n"
        'Content-Type: text/plain; charset="utf-8"\r\n'
        "\r\n"
        "We accept the proposed itinerary.\r\n"
        "--BOUNDARY\r\n"
        'Content-Type: application/pdf; name="passports.pdf"\r\n'
        "Content-Disposition: attachment; filename=passports.pdf\r\n"
        "Content-Transfer-Encoding: base64\r\n"
        "\r\n"
        "c3ludGhldGlj\r\n"
        "--BOUNDARY--\r\n"
    )
    inbound = parse_raw_email(raw)
    assert inbound.external_id == "msg-reply-99@example.test"
    assert inbound.external_thread_id == "original-thread-42@example.test"
    assert inbound.body == "We accept the proposed itinerary."
    assert "attachment:passports.pdf" in inbound.media_refs


def test_null_transport_does_not_raise() -> None:
    NullTransport().deliver(to="x@example.test", subject="hi", body="body", message_id="test-1")


def test_smtp_transport_config_defaults() -> None:
    cfg = SMTPTransportConfig(host="localhost", port=1025)
    assert cfg.host == "localhost"
    assert cfg.port == 1025
    assert cfg.use_tls is False
