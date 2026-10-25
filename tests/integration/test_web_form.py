"""Public web form validation, spam controls, privacy, and idempotency."""

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text

from travelos.channels.models import Conversation, Message
from travelos.crm.models import Contact, Customer

pytestmark = pytest.mark.integration


def form_payload(*, submission_id: UUID | None = None, **updates: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "submission_id": str(submission_id or uuid4()),
        "name": "Taylor Example",
        "email": "taylor@example.test",
        "phone": None,
        "message": "Please help plan a synthetic trip.",
        "website": "",
    }
    payload.update(updates)
    return payload


def test_web_form_accepts_duplicate_once_and_keeps_unverified_claims_unlinked(
    auth_client, db_factory, test_settings
) -> None:
    with db_factory() as db:
        customer = Customer(customer_type="individual", display_name="Existing Example")
        db.add(customer)
        db.flush()
        contact = Contact(
            customer_id=customer.id,
            channel="email",
            value="taylor@example.test",
            value_hash="a" * 64,
            verified=True,
            is_primary=True,
        )
        db.add(contact)
        db.commit()

    payload = form_payload()
    first = auth_client.post("/channels/web-form", json=payload)
    replay = auth_client.post("/channels/web-form", json=payload)

    assert first.status_code == 202, first.text
    assert first.json()["duplicate"] is False
    assert replay.status_code == 202, replay.text
    assert replay.json()["duplicate"] is True
    with db_factory() as db:
        messages = list(db.execute(select(Message)).scalars())
        assert len(messages) == 1
        conversation = db.get(Conversation, messages[0].conversation_id)
        assert conversation is not None
        assert conversation.customer_id is None
        assert conversation.contact_id is None
        encrypted = db.execute(
            text("SELECT body FROM messages WHERE id = :id"), {"id": messages[0].id}
        ).scalar_one()
        audit = db.execute(
            text("SELECT after FROM audit_log WHERE action = 'channel.inbound_received'")
        ).scalar_one()
    assert encrypted.startswith("enc:v1:")
    assert "taylor@example.test" not in str(audit)
    assert "Taylor Example" not in str(audit)


def test_honeypot_is_accepted_without_creating_a_message(auth_client, db_factory) -> None:
    response = auth_client.post(
        "/channels/web-form", json=form_payload(website="spam.example.test")
    )

    assert response.status_code == 202
    assert response.json()["accepted"] is True
    with db_factory() as db:
        assert db.execute(select(Message.id)).scalars().all() == []


def test_web_form_validates_contact_and_message(auth_client) -> None:
    missing_contact = auth_client.post(
        "/channels/web-form", json=form_payload(email=None, phone=None)
    )
    invalid_email = auth_client.post("/channels/web-form", json=form_payload(email="invalid"))
    empty_message = auth_client.post("/channels/web-form", json=form_payload(message=" "))

    assert missing_contact.status_code == 422
    assert invalid_email.status_code == 422
    assert empty_message.status_code == 422


def test_web_form_rate_limit_is_applied_without_network(auth_client) -> None:
    responses = [auth_client.post("/channels/web-form", json=form_payload()) for _ in range(6)]

    assert [response.status_code for response in responses] == [202] * 5 + [429]
