"""CRM merge preview creates approval without changing customer links."""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient

from travelos.approval.models import ApprovalRequest
from travelos.crm.models import Contact, Customer
from travelos.security.totp import generate_code

pytestmark = pytest.mark.integration


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def token_for(client: TestClient, user, login) -> str:
    code = generate_code(user.mfa_secret) if user.mfa_secret else None
    response = login(client, user, code=code)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_merge_preview_is_human_readable_and_creates_pending_a1_approval(
    auth_client: TestClient, make_user, login, db_factory
) -> None:
    requester = make_user(email="merge-preview-agent@example.test", role="agent")
    headers = bearer(token_for(auth_client, requester, login))
    source_response = auth_client.post(
        "/crm/customers",
        json={"customer_type": "family", "display_name": "Asha Synthetic"},
        headers=headers,
    )
    target_response = auth_client.post(
        "/crm/customers",
        json={"customer_type": "family", "display_name": "Asha Family Synthetic"},
        headers=headers,
    )
    assert source_response.status_code == target_response.status_code == 201
    source_id = source_response.json()["id"]
    target_id = target_response.json()["id"]
    source_contact = auth_client.post(
        f"/crm/customers/{source_id}/contacts",
        json={"channel": "email", "value": "asha.merge-preview@example.test"},
        headers=headers,
    )
    target_contact = auth_client.post(
        f"/crm/customers/{target_id}/contacts",
        json={"channel": "email", "value": "asha.merge-preview@example.test"},
        headers=headers,
    )
    assert source_contact.status_code == target_contact.status_code == 201

    response = auth_client.post(
        "/crm/merges/preview",
        json={"source_customer_id": source_id, "target_customer_id": target_id},
        headers=headers,
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["source_display_name"] == "Asha Synthetic"
    assert body["target_display_name"] == "Asha Family Synthetic"
    assert body["source_contacts"] == body["target_contacts"] == 1
    assert body["after_target_contacts"] == 2
    assert body["after_target_travellers"] == 0
    assert body["overlapping_contacts"] == [{"channel": "email", "count": 1}]
    assert body["approval_status"] == "pending"

    with db_factory() as db:
        approval = db.get(ApprovalRequest, UUID(body["approval_id"]))
        source = db.get(Customer, UUID(source_id))
        target = db.get(Customer, UUID(target_id))
        source_link = db.get(Contact, UUID(source_contact.json()["id"]))
        target_link = db.get(Contact, UUID(target_contact.json()["id"]))
        assert approval is not None and approval.action_type == "crm.customer_merge"
        assert approval.level == "A1" and approval.status == "pending"
        assert approval.proposed["snapshot_hash"]
        assert source is not None and target is not None
        assert source_link is not None and source_link.customer_id == source.id
        assert target_link is not None and target_link.customer_id == target.id


def test_merge_preview_rejects_same_and_cross_tenant_customers(
    auth_client: TestClient, make_user, login
) -> None:
    requester = make_user(email="merge-tenant-preview@example.test", role="agent")
    other_tenant = make_user(
        email="merge-other-preview@example.test",
        role="agent",
        tenant_id=UUID("00000000-0000-0000-0000-000000000002"),
    )
    headers = bearer(token_for(auth_client, requester, login))
    other_headers = bearer(token_for(auth_client, other_tenant, login))
    customer = auth_client.post(
        "/crm/customers",
        json={"customer_type": "individual", "display_name": "Synthetic Customer"},
        headers=headers,
    ).json()
    same = auth_client.post(
        "/crm/merges/preview",
        json={"source_customer_id": customer["id"], "target_customer_id": customer["id"]},
        headers=headers,
    )
    cross_tenant = auth_client.post(
        "/crm/merges/preview",
        json={
            "source_customer_id": customer["id"],
            "target_customer_id": "00000000-0000-0000-0000-000000000099",
        },
        headers=other_headers,
    )

    assert same.status_code == 422
    assert cross_tenant.status_code == 404
