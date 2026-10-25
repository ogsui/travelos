"""Channel intake and CRM timeline are tenant-scoped and idempotent."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from sqlalchemy import text

from travelos.channels.adapters.mock import (
    InboundMessage,
    MockChannelAdapter,
    OutboundDisabledError,
)
from travelos.core.audit import Actor
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.crm.service import CRMService

pytestmark = pytest.mark.integration


def test_mock_adapter_never_delivers_outbound() -> None:
    with pytest.raises(OutboundDisabledError):
        MockChannelAdapter.send(None)  # type: ignore[arg-type]


def test_mock_inbound_deduplicates_and_timeline_is_newest_first(db_factory) -> None:
    older = datetime.now(UTC) - timedelta(minutes=2)
    newer = datetime.now(UTC) - timedelta(minutes=1)
    tenant = UUID(int=501)
    adapter = MockChannelAdapter()
    with db_factory() as db:
        service = CRMService()
        customer = service.create_customer(
            db,
            customer_type="individual",
            display_name="Casey Example",
            segment=None,
            actor=Actor.system(),
            tenant_id=tenant,
        )
        contact = service.create_contact(
            db,
            customer_id=customer.id,
            channel="email",
            value="casey@example.test",
            verified=True,
            is_primary=True,
            language="en",
            actor=Actor.system(),
            tenant_id=tenant,
        )
        first = adapter.receive(
            db,
            event=InboundMessage(
                channel="email",
                external_id="msg-older",
                external_thread_id="thread-1",
                body="older synthetic message",
                occurred_at=older,
            ),
            tenant_id=tenant,
            customer_id=customer.id,
            contact_id=contact.id,
        )
        replay = adapter.receive(
            db,
            event=InboundMessage(
                channel="email",
                external_id="msg-older",
                external_thread_id="thread-1",
                body="older synthetic message",
                occurred_at=older,
            ),
            tenant_id=tenant,
            customer_id=customer.id,
            contact_id=contact.id,
        )
        second = adapter.receive(
            db,
            event=InboundMessage(
                channel="email",
                external_id="msg-newer",
                external_thread_id="thread-1",
                body="newer synthetic message",
                occurred_at=newer,
            ),
            tenant_id=tenant,
            customer_id=customer.id,
            contact_id=contact.id,
        )
        other_tenant = adapter.receive(
            db,
            event=InboundMessage(
                channel="email",
                external_id="msg-older",
                external_thread_id="thread-1",
                body="other tenant synthetic message",
                occurred_at=older,
            ),
            tenant_id=UUID(int=502),
        )
        assert first.message.id == replay.message.id
        assert other_tenant.message.id != first.message.id
        assert replay.duplicate is True
        assert first.message.conversation_id == second.message.conversation_id
        db.commit()
        rows = adapter.timeline(db, tenant_id=tenant, customer_id=customer.id)
        assert [row.id for row in rows] == [second.message.id, first.message.id]
        assert all(row.conversation.customer_id == customer.id for row in rows)
        other_rows = adapter.timeline(db, tenant_id=UUID(int=502))
        assert [row.id for row in other_rows] == [other_tenant.message.id]


def test_inbound_body_is_encrypted_and_audit_does_not_copy_body(db_factory) -> None:
    tenant = UUID(int=502)
    with db_factory() as db:
        result = MockChannelAdapter().receive(
            db,
            event=InboundMessage(
                channel="web_form",
                external_id="form-1",
                external_thread_id="form-1",
                body="private synthetic request",
                occurred_at=datetime.now(UTC),
            ),
            tenant_id=tenant,
            customer_id=None,
            contact_id=None,
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
    assert "private synthetic request" not in str(audit)


def test_timeline_api_filters_and_requires_crm_read(
    auth_client, make_user, login, db_factory
) -> None:
    agent = make_user(email="timeline-agent@example.test", role="agent")
    accountant = make_user(email="timeline-accountant@example.test", role="accountant")
    agent_headers = {"Authorization": f"Bearer {login(auth_client, agent).json()['access_token']}"}
    accountant_headers = {
        "Authorization": f"Bearer {login(auth_client, accountant).json()['access_token']}"
    }
    with db_factory() as db:
        result = MockChannelAdapter().receive(
            db,
            event=InboundMessage(
                channel="email",
                external_id="api-message-1",
                external_thread_id="api-thread-1",
                body="timeline fixture",
                occurred_at=datetime.now(UTC),
            ),
            tenant_id=DEFAULT_TENANT_ID,
            customer_id=None,
            contact_id=None,
        )
        message_id = result.message.id
        db.commit()

    allowed = auth_client.get("/crm/timeline?channel=email", headers=agent_headers)
    denied = auth_client.get("/crm/timeline", headers=accountant_headers)
    assert allowed.status_code == 200, allowed.text
    assert denied.status_code == 403
    assert [row["id"] for row in allowed.json()] == [str(message_id)]
