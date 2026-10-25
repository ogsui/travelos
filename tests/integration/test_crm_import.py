"""CRM imports require a matching approval and create records idempotently."""

from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from travelos.core.models import IdempotencyKey
from travelos.crm.models import Consent, Contact, Customer
from travelos.security.totp import generate_code

pytestmark = pytest.mark.integration


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def token_for(client: TestClient, user, login) -> str:
    code = generate_code(user.mfa_secret) if user.mfa_secret else None
    response = login(client, user, code=code)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def import_payload() -> dict[str, object]:
    content = (
        b"Customer Name,Email Address,Marketing Opt In,Consent Reference\n"
        b"Asha Synthetic,asha.import@example.test,Yes,proof-001\n"
        b"Balu Synthetic,balu.import@example.test,maybe,\n"
        b"Duplicate Synthetic,asha.import@example.test,Yes,proof-002\n"
    )
    return {
        "filename": "customers.csv",
        "content_base64": base64.b64encode(content).decode("ascii"),
        "mapping": {
            "name": "Customer Name",
            "email": "Email Address",
            "email_marketing_consent": "Marketing Opt In",
            "email_marketing_proof_ref": "Consent Reference",
        },
    }


def test_crm_import_preview_approval_apply_and_replay(
    auth_client: TestClient, make_user, login, db_factory
) -> None:
    requester = make_user(email="import-agent@example.test", role="agent")
    reviewer = make_user(email="import-owner@example.test", role="owner", mfa_enrolled=True)
    requester_headers = bearer(token_for(auth_client, requester, login))
    reviewer_headers = bearer(token_for(auth_client, reviewer, login))
    payload = import_payload()

    preview = auth_client.post("/crm/imports/preview", json=payload, headers=requester_headers)
    assert preview.status_code == 200, preview.text
    preview_body = preview.json()
    assert preview_body["rows_seen"] == 3
    assert preview_body["valid_rows"] == 3
    assert preview_body["importable_rows"] == 2
    assert preview_body["duplicate_candidates"][0]["duplicate_row"] == 4
    assert "asha.import@example.test" not in str(preview_body)
    approval_id = preview_body["approval_id"]

    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(Customer)) == 0

    premature = auth_client.post(
        f"/crm/imports/apply/{approval_id}", json=payload, headers=requester_headers
    )
    assert premature.status_code == 409

    approved = auth_client.post(
        f"/approvals/{approval_id}/decision",
        json={"decision": "approve"},
        headers=reviewer_headers,
    )
    assert approved.status_code == 200, approved.text
    assert approved.json()["status"] == "approved"

    applied = auth_client.post(
        f"/crm/imports/apply/{approval_id}", json=payload, headers=requester_headers
    )
    assert applied.status_code == 200, applied.text
    assert applied.json() == {
        "imported_customers": 2,
        "imported_contacts": 2,
        "granted_consents": 1,
        "duplicate_rows_skipped": 1,
        "already_applied": False,
    }

    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(Customer)) == 2
        assert db.scalar(select(func.count()).select_from(Contact)) == 2
        assert db.scalar(select(func.count()).select_from(Consent)) == 1

    replay = auth_client.post(
        f"/crm/imports/apply/{approval_id}", json=payload, headers=requester_headers
    )
    assert replay.status_code == 200, replay.text
    assert replay.json()["already_applied"] is True
    assert replay.json()["imported_customers"] == 0


def test_import_approval_is_bound_to_exact_batch_and_mapping(
    auth_client: TestClient, make_user, login
) -> None:
    requester = make_user(email="import-bind-agent@example.test", role="agent")
    reviewer = make_user(email="import-bind-owner@example.test", role="owner", mfa_enrolled=True)
    requester_headers = bearer(token_for(auth_client, requester, login))
    reviewer_headers = bearer(token_for(auth_client, reviewer, login))
    payload = import_payload()
    preview = auth_client.post("/crm/imports/preview", json=payload, headers=requester_headers)
    assert preview.status_code == 200, preview.text
    approval_id = preview.json()["approval_id"]
    approved = auth_client.post(
        f"/approvals/{approval_id}/decision",
        json={"decision": "approve"},
        headers=reviewer_headers,
    )
    assert approved.status_code == 200

    altered_mapping = dict(payload)
    altered_mapping["mapping"] = {**payload["mapping"], "name": "Email Address"}
    mapping_response = auth_client.post(
        f"/crm/imports/apply/{approval_id}",
        json=altered_mapping,
        headers=requester_headers,
    )
    assert mapping_response.status_code == 409

    altered = dict(payload)
    altered["content_base64"] = base64.b64encode(
        b"Customer Name,Email Address,Marketing Opt In,Consent Reference\n"
        b"Other Synthetic,other@example.test,No,\n"
    ).decode()
    response = auth_client.post(
        f"/crm/imports/apply/{approval_id}", json=altered, headers=requester_headers
    )

    assert response.status_code == 409


def test_crm_import_failure_rolls_back_records_and_idempotency_claim(
    auth_client: TestClient, make_user, login, db_factory, monkeypatch
) -> None:
    requester = make_user(email="import-fail-agent@example.test", role="agent")
    reviewer = make_user(email="import-fail-owner@example.test", role="owner", mfa_enrolled=True)
    requester_headers = bearer(token_for(auth_client, requester, login))
    reviewer_headers = bearer(token_for(auth_client, reviewer, login))
    payload = import_payload()
    preview = auth_client.post("/crm/imports/preview", json=payload, headers=requester_headers)
    assert preview.status_code == 200, preview.text
    approval_id = preview.json()["approval_id"]
    approved = auth_client.post(
        f"/approvals/{approval_id}/decision",
        json={"decision": "approve"},
        headers=reviewer_headers,
    )
    assert approved.status_code == 200

    def fail_contact_creation(*args, **kwargs):
        raise RuntimeError("synthetic import failure")

    monkeypatch.setattr(
        "travelos.crm.import_service.CRMService.create_contact", fail_contact_creation
    )
    with pytest.raises(RuntimeError, match="synthetic import failure"):
        auth_client.post(
            f"/crm/imports/apply/{approval_id}", json=payload, headers=requester_headers
        )

    with db_factory() as db:
        assert db.scalar(select(func.count()).select_from(Customer)) == 0
        assert db.scalar(select(func.count()).select_from(Contact)) == 0
        assert db.scalar(select(func.count()).select_from(IdempotencyKey)) == 0


def test_crm_import_write_denies_admin(auth_client: TestClient, make_user, login) -> None:
    admin = make_user(email="import-admin@example.test", role="admin", mfa_enrolled=True)
    headers = bearer(token_for(auth_client, admin, login))

    response = auth_client.post("/crm/imports/preview", json=import_payload(), headers=headers)

    assert response.status_code == 403
