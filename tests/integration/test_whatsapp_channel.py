"""Integration tests for the WhatsApp channel adapter and webhook endpoints.

Tests run against a real PostgreSQL database (pgserver) and verify:
- Inbound WhatsApp Cloud API webhook ingestion and message deduplication
- Conversation threading by sender phone number
- 24-hour customer care service window enforcement against database records
- Public webhook challenge handshake and signature validation
- Outbound safety gate enforcement in live mode
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from travelos.channels.adapters.whatsapp import (
    MockWhatsAppTransport,
    WhatsAppChannelAdapter,
    WhatsAppSendBlockedError,
)
from travelos.channels.models import Conversation, Message
from travelos.core.config import Mode

pytestmark = pytest.mark.integration


def make_whatsapp_payload(
    msg_id: str = "wamid.INTEG1",
    from_num: str = "919876543210",
    text_body: str = "Hello from WhatsApp",
) -> dict[str, object]:
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WABA_TEST",
                "changes": [
                    {
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {"display_phone_number": "123", "phone_number_id": "456"},
                            "contacts": [{"wa_id": from_num, "profile": {"name": "Test User"}}],
                            "messages": [
                                {
                                    "from": from_num,
                                    "id": msg_id,
                                    "timestamp": str(int(datetime.now(UTC).timestamp())),
                                    "type": "text",
                                    "text": {"body": text_body},
                                }
                            ],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


# ---------------------------------------------------------------------------
# Database-backed Inbound & Deduplication
# ---------------------------------------------------------------------------


def test_whatsapp_inbound_deduplicates(db_factory) -> None:  # type: ignore[no-untyped-def]
    tenant = UUID(int=701)
    adapter = WhatsAppChannelAdapter()
    payload = make_whatsapp_payload(msg_id="wamid.DEDUP1", from_num="919876543210")

    with db_factory() as db:
        first_results = adapter.receive_webhook(db, payload=payload, tenant_id=tenant)
        db.commit()
        replay_results = adapter.receive_webhook(db, payload=payload, tenant_id=tenant)
        db.commit()

        assert len(first_results) == 1
        assert len(replay_results) == 1
        assert first_results[0].message.id == replay_results[0].message.id
        assert first_results[0].duplicate is False
        assert replay_results[0].duplicate is True

        stored_body = db.execute(
            text("SELECT body FROM messages WHERE id = :id"),
            {"id": first_results[0].message.id},
        ).scalar_one()
        assert stored_body.startswith("enc:v1:")


def test_whatsapp_conversation_threading_updates_last_inbound(db_factory) -> None:  # type: ignore[no-untyped-def]
    tenant = UUID(int=702)
    adapter = WhatsAppChannelAdapter()
    from_num = "919999988888"

    payload1 = make_whatsapp_payload(msg_id="wamid.T1", from_num=from_num, text_body="Msg 1")
    payload2 = make_whatsapp_payload(msg_id="wamid.T2", from_num=from_num, text_body="Msg 2")

    with db_factory() as db:
        r1 = adapter.receive_webhook(db, payload=payload1, tenant_id=tenant)
        db.commit()
        r2 = adapter.receive_webhook(db, payload=payload2, tenant_id=tenant)
        db.commit()

        assert r1[0].conversation.id == r2[0].conversation.id
        conv = db.get(Conversation, r1[0].conversation.id)
        assert conv is not None
        assert conv.last_inbound_at is not None


# ---------------------------------------------------------------------------
# 24-hour Window Enforcement with DB
# ---------------------------------------------------------------------------


def test_whatsapp_send_enforces_24h_window_from_db(db_factory) -> None:  # type: ignore[no-untyped-def]
    tenant = UUID(int=703)
    transport = MockWhatsAppTransport()
    adapter = WhatsAppChannelAdapter(
        transport=transport,
        mode=Mode.LIVE,
        kill_switch_enabled=True,
    )
    from_num = "919111122222"

    payload = make_whatsapp_payload(msg_id="wamid.WIN1", from_num=from_num, text_body="Inquiry")

    with db_factory() as db:
        res = adapter.receive_webhook(db, payload=payload, tenant_id=tenant)
        db.commit()
        msg_inbound = res[0].message

        # Case 1: Send within 24h succeeds without template
        outbound_msg1 = Message(
            tenant_id=tenant,
            conversation_id=msg_inbound.conversation_id,
            direction="outbound",
            channel="whatsapp",
            body="Here are the travel options.",
            external_id="outbound-wa-1",
            occurred_at=datetime.now(UTC),
        )
        db.add(outbound_msg1)
        db.flush()

        adapter.send(
            outbound_msg1,
            db=db,
            to=from_num,
            tenant_id=tenant,
        )
        assert len(transport.sent) == 1
        assert transport.sent[0]["template_id"] is None

        # Case 2: Advance time past 24h -> free-form blocked, template required
        future_time = datetime.now(UTC) + timedelta(hours=25)
        outbound_msg2 = Message(
            tenant_id=tenant,
            conversation_id=msg_inbound.conversation_id,
            direction="outbound",
            channel="whatsapp",
            body="Follow up message",
            external_id="outbound-wa-2",
            occurred_at=future_time,
        )
        db.add(outbound_msg2)
        db.flush()

        with pytest.raises(
            WhatsAppSendBlockedError, match="outside 24-hour window requires an approved template"
        ):
            adapter.send(
                outbound_msg2,
                db=db,
                to=from_num,
                tenant_id=tenant,
                now=future_time,
            )

        # Case 3: Send with approved template succeeds outside 24h
        adapter.send(
            outbound_msg2,
            db=db,
            to=from_num,
            tenant_id=tenant,
            template_id="payment_reminder",
            now=future_time,
        )
        assert len(transport.sent) == 2
        assert transport.sent[1]["template_id"] == "payment_reminder"


# ---------------------------------------------------------------------------
# HTTP Webhook Endpoint Tests
# ---------------------------------------------------------------------------


def test_whatsapp_webhook_get_verification(auth_client: TestClient, test_settings) -> None:  # type: ignore[no-untyped-def]
    verify_token = "secret-token-42"
    auth_client.app.state.settings = test_settings.model_copy(
        update={"whatsapp_verify_token": verify_token}
    )

    valid_res = auth_client.get(
        "/channels/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=secret-token-42&hub.challenge=12345"
    )
    assert valid_res.status_code == 200
    assert valid_res.text == "12345"

    invalid_res = auth_client.get(
        "/channels/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=wrong-token&hub.challenge=12345"
    )
    assert invalid_res.status_code == 403


def test_whatsapp_webhook_post_ingestion_and_signature(
    auth_client: TestClient, test_settings, db_factory
) -> None:  # type: ignore[no-untyped-def]
    app_secret = "meta-app-secret-789"
    auth_client.app.state.settings = test_settings.model_copy(
        update={"whatsapp_app_secret": app_secret}
    )

    payload = make_whatsapp_payload(
        msg_id="wamid.API1", from_num="919777766666", text_body="API test message"
    )
    raw_bytes = json.dumps(payload).encode("utf-8")
    valid_sig = (
        "sha256=" + hmac.new(app_secret.encode("utf-8"), raw_bytes, hashlib.sha256).hexdigest()
    )

    # Invalid signature rejected
    bad_sig_res = auth_client.post(
        "/channels/whatsapp/webhook",
        content=raw_bytes,
        headers={"X-Hub-Signature-256": "sha256=invalid", "Content-Type": "application/json"},
    )
    assert bad_sig_res.status_code == 403

    # Valid signature accepted
    ok_res = auth_client.post(
        "/channels/whatsapp/webhook",
        content=raw_bytes,
        headers={"X-Hub-Signature-256": valid_sig, "Content-Type": "application/json"},
    )
    assert ok_res.status_code == 200
    assert ok_res.json()["status"] == "ok"
    assert ok_res.json()["received"] == 1

    # A missing signature is not accepted while the app secret is configured
    unsigned_res = auth_client.post(
        "/channels/whatsapp/webhook",
        content=raw_bytes,
        headers={"Content-Type": "application/json"},
    )
    assert unsigned_res.status_code == 403


def test_whatsapp_webhook_malformed_json_returns_422(
    auth_client: TestClient,
    test_settings,  # type: ignore[no-untyped-def]
) -> None:
    auth_client.app.state.settings = test_settings.model_copy(update={"whatsapp_app_secret": None})

    response = auth_client.post(
        "/channels/whatsapp/webhook",
        content=b"{not-json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422


def test_whatsapp_webhook_fails_closed_without_credentials_outside_test_mode(
    auth_client: TestClient,
    test_settings,  # type: ignore[no-untyped-def]
) -> None:
    auth_client.app.state.settings = test_settings.model_copy(
        update={
            "mode": Mode.LIVE,
            "whatsapp_app_secret": None,
            "whatsapp_verify_token": None,
        }
    )

    get_res = auth_client.get(
        "/channels/whatsapp/webhook?hub.mode=subscribe&hub.verify_token=any&hub.challenge=1"
    )
    post_res = auth_client.post(
        "/channels/whatsapp/webhook",
        content=b"{}",
        headers={"Content-Type": "application/json"},
    )

    assert get_res.status_code == 503
    assert post_res.status_code == 503
    assert post_res.json()["error"] == "whatsapp_webhook_not_configured"
