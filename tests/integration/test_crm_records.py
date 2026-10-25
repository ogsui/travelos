"""CRM record endpoints enforce tenant boundaries and protect personal data."""

from __future__ import annotations

from datetime import date
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from travelos.security.totp import generate_code

pytestmark = pytest.mark.integration


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def token_for(client: TestClient, user, login) -> str:
    code = generate_code(user.mfa_secret) if user.mfa_secret else None
    response = login(client, user, code=code)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_customer_contact_and_traveller_crud_encrypts_pii(
    auth_client: TestClient, make_user, login, db_factory
) -> None:
    user = make_user(email="records-agent@example.test", role="agent")
    headers = bearer(token_for(auth_client, user, login))

    created_customer = auth_client.post(
        "/crm/customers",
        json={
            "customer_type": "family",
            "display_name": "Ravi Synthetic",
            "segment": "family",
        },
        headers=headers,
    )
    assert created_customer.status_code == 201, created_customer.text
    customer_id = created_customer.json()["id"]
    assert created_customer.json()["display_name"] == "Ravi Synthetic"

    created_contact = auth_client.post(
        f"/crm/customers/{customer_id}/contacts",
        json={
            "channel": "email",
            "value": "ravi.synthetic@example.test",
            "verified": True,
            "is_primary": True,
            "language": "en",
        },
        headers=headers,
    )
    assert created_contact.status_code == 201, created_contact.text
    assert created_contact.json()["value"] == "ravi.synthetic@example.test"

    created_traveller = auth_client.post(
        f"/crm/customers/{customer_id}/travellers",
        json={
            "full_name_as_passport": "RAVI SYNTHETIC",
            "dob": "1985-01-01",
            "gender": "unspecified",
            "passport_no": "F7000001",
            "passport_expiry": "2030-12-31",
            "nationality": "IN",
            "is_minor": False,
            "meal_pref": None,
            "seat_pref": None,
        },
        headers=headers,
    )
    assert created_traveller.status_code == 201, created_traveller.text
    assert created_traveller.json()["dob"] == date(1985, 1, 1).isoformat()

    with db_factory() as db:
        encrypted_name = db.execute(
            text("SELECT display_name FROM customers WHERE id = :id"), {"id": customer_id}
        ).scalar_one()
        encrypted_contact = db.execute(
            text("SELECT value FROM contacts WHERE customer_id = :id"), {"id": customer_id}
        ).scalar_one()
        encrypted_traveller = db.execute(
            text("SELECT passport_no, dob FROM travellers WHERE customer_id = :id"),
            {"id": customer_id},
        ).one()

    assert encrypted_name.startswith("enc:v1:")
    assert encrypted_contact.startswith("enc:v1:")
    assert encrypted_traveller.passport_no.startswith("enc:v1:")
    assert encrypted_traveller.dob.startswith("enc:v1:")
    assert "Ravi Synthetic" not in encrypted_name
    assert "ravi.synthetic@example.test" not in encrypted_contact

    customer_list = auth_client.get("/crm/customers", headers=headers)
    assert customer_list.status_code == 200
    assert any(row["id"] == customer_id for row in customer_list.json())
    contact_list = auth_client.get(f"/crm/customers/{customer_id}/contacts", headers=headers)
    traveller_list = auth_client.get(f"/crm/customers/{customer_id}/travellers", headers=headers)
    assert contact_list.status_code == 200 and len(contact_list.json()) == 1
    assert traveller_list.status_code == 200 and len(traveller_list.json()) == 1


def test_customer_and_children_are_tenant_scoped(auth_client: TestClient, make_user, login) -> None:
    owner = make_user(email="records-owner@example.test", role="agent")
    user = make_user(
        email="records-other-tenant@example.test", role="agent", tenant_id=UUID(int=99)
    )
    headers = bearer(token_for(auth_client, user, login))
    created = auth_client.post(
        "/crm/customers",
        json={"customer_type": "individual", "display_name": "Other Synthetic"},
        headers=bearer(token_for(auth_client, owner, login)),
    )
    assert created.status_code == 201, created.text
    customer_id = created.json()["id"]

    assert auth_client.get(f"/crm/customers/{customer_id}", headers=headers).status_code == 404
    assert (
        auth_client.get(f"/crm/customers/{customer_id}/contacts", headers=headers).status_code
        == 404
    )


def test_customer_write_denies_admin(auth_client: TestClient, make_user, login) -> None:
    admin = make_user(email="records-admin@example.test", role="admin", mfa_enrolled=True)
    response = auth_client.post(
        "/crm/customers",
        json={"customer_type": "individual", "display_name": "Denied Synthetic"},
        headers=bearer(token_for(auth_client, admin, login)),
    )

    assert response.status_code == 403


def test_customer_type_validation_is_closed(auth_client: TestClient, make_user, login) -> None:
    user = make_user(email="records-validation@example.test", role="agent")
    response = auth_client.post(
        "/crm/customers",
        json={"customer_type": "unknown", "display_name": "Invalid Synthetic"},
        headers=bearer(token_for(auth_client, user, login)),
    )

    assert response.status_code == 422
