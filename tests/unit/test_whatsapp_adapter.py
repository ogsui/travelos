"""Unit tests for the WhatsApp Business Cloud API adapter.

Covers:
- Meta webhook signature verification and subscription challenge
- Meta 24-hour customer care service window logic
- Cloud API webhook payload parsing (text, images, docs, buttons, audio)
- Outbound safety gates: mode gate, kill switch, consent, approval, and 24h template rule
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, ClassVar
from uuid import UUID, uuid4

import httpx
import pytest
from httpx import HTTPStatusError, Request, Response

from travelos.channels.adapters.whatsapp import (
    CloudAPIWhatsAppTransport,
    MockWhatsAppTransport,
    WhatsAppChannelAdapter,
    WhatsAppPayloadError,
    WhatsAppSendBlockedError,
    is_approved_template,
    is_within_24h_window,
    parse_whatsapp_webhook,
    verify_meta_signature,
    verify_webhook_challenge,
)
from travelos.core.config import Mode

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "whatsapp" / "webhook_events.json"


def _recorded_events() -> list[dict[str, Any]]:
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    return list(data["events"])


class FakeConversation:
    def __init__(self, last_inbound_at: datetime | None = None) -> None:
        self.last_inbound_at = last_inbound_at


class FakeMessage:
    def __init__(
        self,
        *,
        body: str = "synthetic whatsapp body",
        msg_id: UUID | None = None,
        conversation: FakeConversation | None = None,
    ) -> None:
        self.id = msg_id or uuid4()
        self.body = body
        self.channel = "whatsapp"
        self.direction = "outbound"
        self.status = "received"
        self.conversation = conversation or FakeConversation()
        self.conversation_id = uuid4()
        self.template_id = None


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
# Signature verification tests
# ---------------------------------------------------------------------------


def test_verify_meta_signature_valid() -> None:
    secret = "test-app-secret-12345"
    body = b'{"object":"whatsapp_business_account"}'
    sig = "sha256=" + hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    assert verify_meta_signature(body, sig, secret) is True


def test_verify_meta_signature_invalid() -> None:
    secret = "test-app-secret-12345"
    body = b'{"object":"whatsapp_business_account"}'
    assert verify_meta_signature(body, "sha256=invalid-signature", secret) is False
    assert verify_meta_signature(body, "invalid-header-without-prefix", secret) is False
    assert verify_meta_signature(body, None, secret) is False
    assert verify_meta_signature(body, "", secret) is False


def test_verify_webhook_challenge() -> None:
    token = "meta-verify-secret-999"
    challenge = "1122334455"
    assert (
        verify_webhook_challenge(
            mode="subscribe", token=token, challenge=challenge, expected_token=token
        )
        == challenge
    )
    assert (
        verify_webhook_challenge(
            mode="subscribe", token="wrong", challenge=challenge, expected_token=token
        )
        is None
    )
    assert (
        verify_webhook_challenge(
            mode="other", token=token, challenge=challenge, expected_token=token
        )
        is None
    )


# ---------------------------------------------------------------------------
# 24-hour service window tests
# ---------------------------------------------------------------------------


def test_is_within_24h_window() -> None:
    now = datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)
    inside = now - timedelta(hours=23, minutes=59)
    outside = now - timedelta(hours=24, minutes=1)
    future = now + timedelta(minutes=5)

    assert is_within_24h_window(inside, now=now) is True
    assert is_within_24h_window(outside, now=now) is False
    assert is_within_24h_window(None, now=now) is False
    assert is_within_24h_window(future, now=now) is False
    # Naive inputs are treated as UTC rather than raising.
    assert is_within_24h_window(inside.replace(tzinfo=None), now=now.replace(tzinfo=None)) is True


def test_approved_templates() -> None:
    for name in ("quote_ready", "instant_acknowledgement", "booking_confirmed"):
        assert is_approved_template(name) is True
    assert is_approved_template("random_unapproved_template") is False


# ---------------------------------------------------------------------------
# Cloud API Webhook payload parsing tests
# ---------------------------------------------------------------------------


def test_parse_whatsapp_webhook_text_and_media() -> None:
    payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_123",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "123", "phone_number_id": "456"},
                            "contacts": [{"wa_id": "919876543210", "profile": {"name": "Test"}}],
                            "messages": [
                                {
                                    "from": "919876543210",
                                    "id": "wamid.TEXT1",
                                    "timestamp": "1730000000",
                                    "type": "text",
                                    "text": {"body": "Looking for holiday packages"},
                                },
                                {
                                    "from": "919876543210",
                                    "id": "wamid.IMG1",
                                    "timestamp": "1730000005",
                                    "type": "image",
                                    "image": {"id": "media-img-123", "caption": "Passport copy"},
                                },
                                {
                                    "from": "919876543210",
                                    "id": "wamid.DOC1",
                                    "timestamp": "1730000010",
                                    "type": "document",
                                    "document": {
                                        "id": "media-doc-456",
                                        "filename": "itinerary.pdf",
                                        "caption": "",
                                    },
                                },
                                {
                                    "from": "919876543210",
                                    "id": "wamid.BTN1",
                                    "timestamp": "1730000015",
                                    "type": "interactive",
                                    "interactive": {
                                        "type": "button_reply",
                                        "button_reply": {"id": "btn_yes", "title": "Accept Quote"},
                                    },
                                },
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }

    messages = parse_whatsapp_webhook(payload)
    assert len(messages) == 4

    assert messages[0].external_id == "wamid.TEXT1"
    assert messages[0].external_thread_id == "919876543210"
    assert messages[0].body == "Looking for holiday packages"

    assert messages[1].external_id == "wamid.IMG1"
    assert messages[1].body == "Passport copy"
    assert "whatsapp:image:media-img-123" in messages[1].media_refs

    assert messages[2].external_id == "wamid.DOC1"
    assert "itinerary.pdf" in messages[2].body
    assert "whatsapp:document:media-doc-456:itinerary.pdf" in messages[2].media_refs

    assert messages[3].external_id == "wamid.BTN1"
    assert messages[3].body == "Accept Quote"


@pytest.mark.parametrize("case", _recorded_events(), ids=lambda case: case["name"])
def test_recorded_cloud_api_fixtures(case: dict[str, Any]) -> None:
    """Contract: recorded Cloud API payloads normalize to the expected events."""
    messages = parse_whatsapp_webhook(case["payload"])

    assert len(messages) == len(case["expected"])
    for actual, expected in zip(messages, case["expected"], strict=True):
        assert actual.channel == "whatsapp"
        assert actual.external_id == expected["external_id"]
        assert actual.external_thread_id == expected["thread"]
        assert actual.body == expected["body"]
        assert list(actual.media_refs) == expected["media_refs"]
        assert actual.occurred_at.isoformat() == expected["occurred_at"]


def test_recorded_fixture_bytes_match_mapping() -> None:
    """The endpoint hands the parser raw bytes; both input shapes must agree."""
    payload = _recorded_events()[0]["payload"]
    raw = json.dumps(payload).encode("utf-8")

    assert parse_whatsapp_webhook(payload) == parse_whatsapp_webhook(raw)
    assert parse_whatsapp_webhook(json.dumps(payload)) == parse_whatsapp_webhook(raw)


@pytest.mark.parametrize(
    "payload",
    [b"{not-json", "{not-json", b"\xff\xfe", [], "[]"],
)
def test_malformed_webhook_payloads_raise(payload: object) -> None:
    with pytest.raises(WhatsAppPayloadError):
        parse_whatsapp_webhook(payload)  # type: ignore[arg-type]


def test_malformed_entries_are_skipped_without_losing_the_batch() -> None:
    payload = {
        "entry": [
            "not-an-object",
            {"changes": [{"value": {"messaging_product": "whatsapp", "messages": ["bad"]}}]},
            {
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "messages": [
                                {
                                    "from": "919000000108",
                                    "id": "wamid.SYNTH.KEEP.1",
                                    "timestamp": "1730000000",
                                    "type": "text",
                                    "text": {"body": "kept"},
                                }
                            ],
                        }
                    }
                ]
            },
        ]
    }

    messages = parse_whatsapp_webhook(payload)

    assert [message.external_id for message in messages] == ["wamid.SYNTH.KEEP.1"]


# ---------------------------------------------------------------------------
# Outbound safety gates
# ---------------------------------------------------------------------------


def test_send_blocked_in_test_mode() -> None:
    adapter = WhatsAppChannelAdapter(mode=Mode.TEST, kill_switch_enabled=True)
    msg = FakeMessage()
    with pytest.raises(WhatsAppSendBlockedError, match="test mode"):
        adapter.send(msg, to="919876543210")


def test_send_blocked_in_shadow_mode() -> None:
    adapter = WhatsAppChannelAdapter(mode=Mode.SHADOW, kill_switch_enabled=True)
    msg = FakeMessage()
    with pytest.raises(WhatsAppSendBlockedError, match="shadow mode"):
        adapter.send(msg, to="919876543210")


def test_send_blocked_when_kill_switch_disabled() -> None:
    adapter = WhatsAppChannelAdapter(mode=Mode.LIVE, kill_switch_enabled=False)
    msg = FakeMessage()
    with pytest.raises(WhatsAppSendBlockedError, match="kill switch"):
        adapter.send(msg, to="919876543210")


def test_send_blocked_without_consent() -> None:
    adapter = WhatsAppChannelAdapter(
        mode=Mode.LIVE,
        kill_switch_enabled=True,
        consent_checker=DenyAllConsent(),  # type: ignore[arg-type]
    )
    msg = FakeMessage()
    with pytest.raises(WhatsAppSendBlockedError, match="consent"):
        adapter.send(
            msg,
            db=DummySession(),  # type: ignore[arg-type]
            to="919876543210",
            contact_id=uuid4(),
        )


def test_send_blocked_when_action_requires_approval() -> None:
    adapter = WhatsAppChannelAdapter(
        mode=Mode.LIVE,
        kill_switch_enabled=True,
    )
    msg = FakeMessage()
    with pytest.raises(WhatsAppSendBlockedError, match="approval is required for action"):
        adapter.send(
            msg,
            db=DummySession(),  # type: ignore[arg-type]
            to="919876543210",
            action_type="message.sales_reply",
        )


def test_send_outside_24h_window_requires_template() -> None:
    now = datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)
    old_time = now - timedelta(hours=30)
    adapter = WhatsAppChannelAdapter(
        mode=Mode.LIVE,
        kill_switch_enabled=True,
        consent_checker=AllowAllConsent(),  # type: ignore[arg-type]
    )
    msg = FakeMessage(conversation=FakeConversation(last_inbound_at=old_time))

    # Without template -> blocked
    with pytest.raises(WhatsAppSendBlockedError, match="requires an approved template"):
        adapter.send(
            msg,
            db=DummySession(),  # type: ignore[arg-type]
            to="919876543210",
            now=now,
        )

    # With unapproved template -> blocked
    with pytest.raises(WhatsAppSendBlockedError, match="not an approved template"):
        adapter.send(
            msg,
            db=DummySession(),  # type: ignore[arg-type]
            to="919876543210",
            template_id="unapproved_marketing_blast",
            now=now,
        )


def test_send_outside_24h_window_with_approved_template_succeeds() -> None:
    now = datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)
    old_time = now - timedelta(hours=30)
    transport = MockWhatsAppTransport()
    adapter = WhatsAppChannelAdapter(
        transport=transport,
        mode=Mode.LIVE,
        kill_switch_enabled=True,
        consent_checker=AllowAllConsent(),  # type: ignore[arg-type]
        audit=DummyAuditWriter(),  # type: ignore[arg-type]
    )
    msg = FakeMessage(conversation=FakeConversation(last_inbound_at=old_time))
    adapter.send(
        msg,
        db=DummySession(),  # type: ignore[arg-type]
        to="919876543210",
        template_id="quote_ready",
        now=now,
    )
    assert len(transport.sent) == 1
    assert transport.sent[0]["template_id"] == "quote_ready"
    assert transport.sent[0]["to"] == "919876543210"


def test_unapproved_template_is_blocked_even_inside_window() -> None:
    now = datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)
    adapter = WhatsAppChannelAdapter(
        mode=Mode.LIVE,
        kill_switch_enabled=True,
        consent_checker=AllowAllConsent(),  # type: ignore[arg-type]
    )
    msg = FakeMessage(conversation=FakeConversation(last_inbound_at=now - timedelta(hours=1)))

    with pytest.raises(WhatsAppSendBlockedError, match="not an approved template"):
        adapter.send(
            msg,
            db=DummySession(),  # type: ignore[arg-type]
            to="919876543210",
            template_id="unapproved_marketing_blast",
            now=now,
        )


def test_send_inside_24h_window_free_form_succeeds() -> None:
    now = datetime(2026, 10, 25, 12, 0, 0, tzinfo=UTC)
    recent = now - timedelta(hours=2)
    transport = MockWhatsAppTransport()
    adapter = WhatsAppChannelAdapter(
        transport=transport,
        mode=Mode.LIVE,
        kill_switch_enabled=True,
        consent_checker=AllowAllConsent(),  # type: ignore[arg-type]
        audit=DummyAuditWriter(),  # type: ignore[arg-type]
    )
    msg = FakeMessage(
        body="Free-form customer support response",
        conversation=FakeConversation(last_inbound_at=recent),
    )
    adapter.send(
        msg,
        db=DummySession(),  # type: ignore[arg-type]
        to="919876543210",
        now=now,
    )
    assert len(transport.sent) == 1
    assert transport.sent[0]["body"] == "Free-form customer support response"
    assert transport.sent[0]["template_id"] is None


# ---------------------------------------------------------------------------
# Cloud API transport payloads (no network)
# ---------------------------------------------------------------------------


class RecordingResponse:
    def __init__(self, error: Exception | None = None) -> None:
        self._error = error

    def raise_for_status(self) -> None:
        if self._error is not None:
            raise self._error


class RecordingClient:
    calls: ClassVar[list[dict[str, Any]]] = []

    def __init__(self, error: Exception | None = None, **_kwargs: object) -> None:
        self._error = error

    def __enter__(self) -> RecordingClient:
        return self

    def __exit__(self, *_exc: object) -> None:
        return None

    def post(self, url: str, *, headers: dict[str, str], json: dict[str, Any]) -> RecordingResponse:
        RecordingClient.calls.append({"url": url, "headers": headers, "json": json})
        return RecordingResponse(self._error)


def test_cloud_api_transport_sends_template_payload(monkeypatch: pytest.MonkeyPatch) -> None:
    RecordingClient.calls = []
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: RecordingClient(**kwargs))
    transport = CloudAPIWhatsAppTransport(
        phone_number_id="SYNTH_PHONE_ID",
        access_token="synthetic-token",
        template_language="en_IN",
    )

    transport.deliver(
        to="+91 98765 43210",
        body="ignored for templates",
        message_id="synthetic-message-id",
        template_id="quote_ready",
        template_params={"components": [{"type": "body"}]},
    )

    call = RecordingClient.calls[0]
    assert call["url"] == "https://graph.facebook.com/v18.0/SYNTH_PHONE_ID/messages"
    assert call["headers"]["Authorization"] == "Bearer synthetic-token"
    payload = call["json"]
    assert payload["to"] == "919876543210"
    assert payload["type"] == "template"
    assert payload["template"]["name"] == "quote_ready"
    assert payload["template"]["language"] == {"code": "en_IN"}
    assert payload["template"]["components"] == [{"type": "body"}]


def test_cloud_api_transport_sends_free_form_text(monkeypatch: pytest.MonkeyPatch) -> None:
    RecordingClient.calls = []
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: RecordingClient(**kwargs))
    transport = CloudAPIWhatsAppTransport(
        phone_number_id="SYNTH_PHONE_ID",
        access_token="synthetic-token",
    )

    transport.deliver(to="919876543210", body="Synthetic reply", message_id="synthetic-message-id")

    payload = RecordingClient.calls[0]["json"]
    assert payload["type"] == "text"
    assert payload["text"] == {"body": "Synthetic reply"}


def test_cloud_api_transport_maps_http_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    error = HTTPStatusError(
        "synthetic failure",
        request=Request("POST", "https://graph.facebook.com/v18.0/x/messages"),
        response=Response(
            500, request=Request("POST", "https://graph.facebook.com/v18.0/x/messages")
        ),
    )
    RecordingClient.calls = []
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: RecordingClient(error=error, **kwargs))
    transport = CloudAPIWhatsAppTransport(
        phone_number_id="SYNTH_PHONE_ID",
        access_token="synthetic-token",
    )

    with pytest.raises(RuntimeError, match="Cloud API delivery error"):
        transport.deliver(
            to="919876543210", body="Synthetic reply", message_id="synthetic-message-id"
        )


def test_cloud_api_transport_rejects_missing_credentials() -> None:
    with pytest.raises(ValueError, match="phone_number_id and access_token"):
        CloudAPIWhatsAppTransport(phone_number_id="", access_token="")
