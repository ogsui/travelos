"""Consent decisions are tenant-scoped, audited and fail closed."""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.core.audit import Actor
from travelos.core.models import AuditLog
from travelos.crm.consent import ConsentService
from travelos.crm.fingerprints import ContactFingerprinter
from travelos.crm.models import Contact, Customer, SuppressionEntry
from travelos.security.encryption import default_key_provider

pytestmark = pytest.mark.integration


def make_contact(db: Session, *, channel: str = "email") -> Contact:
    customer = Customer(customer_type="individual", display_name="Synthetic Customer")
    db.add(customer)
    db.flush()
    value = "customer@example.test" if channel == "email" else "+919000000001"
    contact = Contact(
        customer_id=customer.id,
        channel=channel,
        value=value,
        value_hash=ContactFingerprinter(default_key_provider()).fingerprint(channel, value),
        is_primary=True,
    )
    db.add(contact)
    db.flush()
    return contact


def test_can_send_fails_closed_for_unknown_consent(db: Session) -> None:
    contact = make_contact(db)

    assert not ConsentService().can_send(
        db,
        contact_id=contact.id,
        channel="email",
        purpose="marketing",
    )


def test_can_send_fails_closed_without_a_contact_fingerprint(db: Session) -> None:
    contact = make_contact(db)
    contact.value_hash = None
    ConsentService().grant(
        db,
        contact_id=contact.id,
        channel="email",
        purpose="marketing",
        source="web_form",
        proof_ref="synthetic-no-hash",
        actor=Actor.system(),
    )

    assert not ConsentService().can_send(
        db, contact_id=contact.id, channel="email", purpose="marketing"
    )


def test_can_send_requires_matching_contact_channel_and_purpose(db: Session) -> None:
    contact = make_contact(db)
    service = ConsentService()
    service.grant(
        db,
        contact_id=contact.id,
        channel="email",
        purpose="marketing",
        source="web_form",
        proof_ref="synthetic-proof-01",
        actor=Actor.system(),
    )

    assert service.can_send(db, contact_id=contact.id, channel="email", purpose="marketing")
    assert not service.can_send(db, contact_id=contact.id, channel="email", purpose="reviews")
    assert not service.can_send(db, contact_id=contact.id, channel="whatsapp", purpose="marketing")


def test_withdrawn_consent_and_suppression_both_block(db: Session) -> None:
    contact = make_contact(db)
    service = ConsentService()
    consent = service.grant(
        db,
        contact_id=contact.id,
        channel="email",
        purpose="marketing",
        source="web_form",
        proof_ref="synthetic-proof-02",
        actor=Actor.system(),
    )
    assert service.can_send(db, contact_id=contact.id, channel="email", purpose="marketing")

    service.withdraw(db, consent_id=consent.id, actor=Actor.system())
    assert not service.can_send(db, contact_id=contact.id, channel="email", purpose="marketing")

    # Suppression applies regardless of a matching granted consent record.
    consent.status = "granted"
    consent.withdrawn_at = None
    db.add(
        SuppressionEntry(
            channel="email",
            value_hash=contact.value_hash,
            reason="synthetic_stop",
        )
    )
    db.flush()
    assert not service.can_send(db, contact_id=contact.id, channel="email", purpose="marketing")


def test_consent_changes_are_audited_without_contact_value(db: Session) -> None:
    contact = make_contact(db)
    service = ConsentService()
    consent = service.grant(
        db,
        contact_id=contact.id,
        channel="email",
        purpose="marketing",
        source="web_form",
        proof_ref="synthetic-proof-03",
        actor=Actor.system(),
    )
    service.withdraw(db, consent_id=consent.id, actor=Actor.system())
    db.flush()

    actions = db.execute(select(AuditLog.action)).scalars().all()
    assert actions == ["consent.granted", "consent.withdrawn"]
    audit_text = str(db.execute(select(AuditLog.after)).scalars().all())
    assert "customer@example.test" not in audit_text


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def test_consent_api_grant_check_withdraw(
    auth_client: TestClient, make_user, login, db_factory
) -> None:
    user = make_user(email="crm-agent@example.test", role="agent")
    response = login(auth_client, user)
    assert response.status_code == 200
    token = response.json()["access_token"]

    with db_factory() as db:
        contact = make_contact(db)
        contact_id = contact.id
        db.commit()

    grant = auth_client.post(
        "/crm/consents",
        json={
            "contact_id": str(contact_id),
            "channel": "email",
            "purpose": "marketing",
            "source": "web_form",
            "proof_ref": "synthetic-api-proof",
        },
        headers=bearer(token),
    )
    assert grant.status_code == 201, grant.text
    consent_id = grant.json()["id"]

    allowed = auth_client.get(
        f"/crm/contacts/{contact_id}/can-send?channel=email&purpose=marketing",
        headers=bearer(token),
    )
    assert allowed.status_code == 200
    assert allowed.json() == {"can_send": True}

    withdrawn = auth_client.post(f"/crm/consents/{consent_id}/withdraw", headers=bearer(token))
    assert withdrawn.status_code == 200
    assert withdrawn.json()["status"] == "withdrawn"

    blocked = auth_client.get(
        f"/crm/contacts/{contact_id}/can-send?channel=email&purpose=marketing",
        headers=bearer(token),
    )
    assert blocked.status_code == 200
    assert blocked.json() == {"can_send": False}


def test_consent_api_is_tenant_scoped(
    auth_client: TestClient, make_user, login, db_factory
) -> None:
    user = make_user(email="other-tenant@example.test", role="agent", tenant_id=UUID(int=99))
    login_response = login(auth_client, user)
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    with db_factory() as db:
        contact = make_contact(db)
        contact_id = contact.id
        db.commit()

    response = auth_client.get(
        f"/crm/contacts/{contact_id}/can-send?channel=email&purpose=marketing",
        headers=bearer(token),
    )
    assert response.status_code == 404
