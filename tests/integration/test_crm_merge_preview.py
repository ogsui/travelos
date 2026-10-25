"""CRM merge preview creates approval without changing customer links."""

from __future__ import annotations

from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from travelos.approval.models import ApprovalRequest
from travelos.core.models import AuditLog
from travelos.crm.models import Contact, Customer, CustomerMergeHistory
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


def test_merge_apply_preserves_links_consents_and_primary_flags(
    auth_client: TestClient, make_user, login, db_factory
) -> None:
    from travelos.crm.models import Consent, Traveller

    requester = make_user(email="merge-apply-agent@example.test", role="agent")
    reviewer = make_user(email="merge-apply-owner@example.test", role="owner", mfa_enrolled=True)
    requester_headers = bearer(token_for(auth_client, requester, login))
    reviewer_headers = bearer(token_for(auth_client, reviewer, login))

    def create_customer(name: str) -> str:
        response = auth_client.post(
            "/crm/customers",
            json={"customer_type": "family", "display_name": name},
            headers=requester_headers,
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    source_id, target_id = (
        create_customer("Merge Source Synthetic"),
        create_customer("Merge Target Synthetic"),
    )
    source_contact = auth_client.post(
        f"/crm/customers/{source_id}/contacts",
        json={"channel": "email", "value": "source.merge@example.test", "is_primary": True},
        headers=requester_headers,
    ).json()
    auth_client.post(
        f"/crm/customers/{target_id}/contacts",
        json={"channel": "phone", "value": "+919000000008", "is_primary": True},
        headers=requester_headers,
    ).json()
    source_traveller = auth_client.post(
        f"/crm/customers/{source_id}/travellers",
        json={"full_name_as_passport": "SYNTHETIC TRAVELLER", "is_minor": False},
        headers=requester_headers,
    ).json()
    consent = auth_client.post(
        "/crm/consents",
        json={
            "contact_id": source_contact["id"],
            "channel": "email",
            "purpose": "marketing",
            "source": "synthetic_test",
            "proof_ref": "synthetic-proof",
        },
        headers=requester_headers,
    )
    assert consent.status_code == 201, consent.text
    preview = auth_client.post(
        "/crm/merges/preview",
        json={"source_customer_id": source_id, "target_customer_id": target_id},
        headers=requester_headers,
    )
    assert preview.status_code == 200, preview.text
    approval_id = preview.json()["approval_id"]
    approved = auth_client.post(
        f"/approvals/{approval_id}/decision",
        json={"decision": "approve"},
        headers=reviewer_headers,
    )
    assert approved.status_code == 200

    applied = auth_client.post(f"/crm/merges/apply/{approval_id}", headers=requester_headers)
    assert applied.status_code == 200, applied.text
    assert applied.json()["moved_contacts"] == 1
    assert applied.json()["moved_travellers"] == 1
    replay = auth_client.post(f"/crm/merges/apply/{approval_id}", headers=requester_headers)
    assert replay.status_code == 200 and replay.json()["already_applied"] is True
    active_customers = auth_client.get("/crm/customers", headers=requester_headers)
    assert active_customers.status_code == 200
    assert source_id not in {row["id"] for row in active_customers.json()}
    blocked_contact = auth_client.post(
        f"/crm/customers/{source_id}/contacts",
        json={"channel": "email", "value": "blocked.synthetic@example.test"},
        headers=requester_headers,
    )
    blocked_traveller = auth_client.post(
        f"/crm/customers/{source_id}/travellers",
        json={"full_name_as_passport": "BLOCKED SYNTHETIC"},
        headers=requester_headers,
    )
    assert blocked_contact.status_code == blocked_traveller.status_code == 422

    with db_factory() as db:
        moved_contact = db.get(Contact, UUID(source_contact["id"]))
        moved_traveller = db.get(Traveller, UUID(source_traveller["id"]))
        consent_row = db.get(Consent, UUID(consent.json()["id"]))
        assert moved_contact is not None and moved_contact.customer_id == UUID(target_id)
        assert moved_contact.is_primary is False  # target already had a primary contact
        assert moved_traveller is not None and moved_traveller.customer_id == UUID(target_id)
        assert consent_row is not None and consent_row.contact_id == moved_contact.id
        source = db.get(Customer, UUID(source_id))
        assert source is not None and source.merged_into_customer_id == UUID(target_id)

    other_tenant = make_user(
        email="merge-undo-other-tenant@example.test",
        role="agent",
        tenant_id=UUID("00000000-0000-0000-0000-000000000004"),
    )
    cross_tenant_undo = auth_client.post(
        f"/crm/merges/{approval_id}/undo",
        headers=bearer(token_for(auth_client, other_tenant, login)),
    )
    assert cross_tenant_undo.status_code == 404

    undone = auth_client.post(f"/crm/merges/{approval_id}/undo", headers=requester_headers)
    assert undone.status_code == 200, undone.text
    assert undone.json() == {
        "approval_id": approval_id,
        "restored_contacts": 1,
        "restored_travellers": 1,
        "already_undone": False,
    }
    repeated_undo = auth_client.post(f"/crm/merges/{approval_id}/undo", headers=requester_headers)
    assert repeated_undo.status_code == 200
    assert repeated_undo.json()["already_undone"] is True
    with db_factory() as db:
        restored_contact = db.get(Contact, UUID(source_contact["id"]))
        restored_traveller = db.get(Traveller, UUID(source_traveller["id"]))
        consent_row = db.get(Consent, UUID(consent.json()["id"]))
        assert restored_contact is not None
        assert restored_contact.customer_id == UUID(source_id) and restored_contact.is_primary
        assert restored_traveller is not None and restored_traveller.customer_id == UUID(source_id)
        assert consent_row is not None and consent_row.contact_id == restored_contact.id
        assert db.get(Customer, UUID(source_id)).merged_into_customer_id is None
        merge_events = set(
            db.scalars(
                select(AuditLog.action).where(
                    AuditLog.subject_id == UUID(approval_id),
                    AuditLog.action.in_(
                        {"crm.customer_merge_applied", "crm.customer_merge_undone"}
                    ),
                )
            )
        )
        assert merge_events == {"crm.customer_merge_applied", "crm.customer_merge_undone"}


@pytest.mark.parametrize(
    ("decision", "edited", "stale"),
    [("approve", False, True), ("reject", False, False), ("edit", True, False)],
)
def test_unapproved_edited_rejected_and_stale_merges_are_denied(
    decision: str, edited: bool, stale: bool, auth_client: TestClient, make_user, login, db_factory
) -> None:
    requester = make_user(email=f"merge-safety-{decision}@example.test", role="agent")
    reviewer = make_user(
        email=f"merge-safety-owner-{decision}@example.test", role="owner", mfa_enrolled=True
    )
    requester_headers = bearer(token_for(auth_client, requester, login))
    reviewer_headers = bearer(token_for(auth_client, reviewer, login))

    def customer(name: str) -> str:
        return auth_client.post(
            "/crm/customers",
            json={"customer_type": "individual", "display_name": name},
            headers=requester_headers,
        ).json()["id"]

    source_id, target_id = customer("Safety Source Synthetic"), customer("Safety Target Synthetic")
    preview = auth_client.post(
        "/crm/merges/preview",
        json={"source_customer_id": source_id, "target_customer_id": target_id},
        headers=requester_headers,
    ).json()
    approval_id = preview["approval_id"]
    assert (
        auth_client.post(f"/crm/merges/apply/{approval_id}", headers=requester_headers).status_code
        == 409
    )

    body: dict[str, object] = {"decision": decision}
    if decision == "reject":
        body["reason"] = "synthetic duplicate check failed"
    elif edited:
        body["final"] = {"source_customer_id": source_id}
    review = auth_client.post(
        f"/approvals/{approval_id}/decision", json=body, headers=reviewer_headers
    )
    assert review.status_code == 200
    expected_status = {"approve": "approved", "reject": "rejected", "edit": "edited"}[decision]
    assert review.json()["status"] == expected_status

    if stale:
        assert (
            auth_client.post(
                f"/crm/customers/{source_id}/contacts",
                json={"channel": "email", "value": "changed.after.preview@example.test"},
                headers=requester_headers,
            ).status_code
            == 201
        )
    other = make_user(
        email=f"merge-cross-tenant-{decision}@example.test",
        role="agent",
        tenant_id=UUID("00000000-0000-0000-0000-000000000003"),
    )
    assert (
        auth_client.post(
            f"/crm/merges/apply/{approval_id}",
            headers=bearer(token_for(auth_client, other, login)),
        ).status_code
        == 409
    )
    assert (
        auth_client.post(f"/crm/merges/apply/{approval_id}", headers=requester_headers).status_code
        == 409
    )
    with db_factory() as db:
        assert db.get(Customer, UUID(source_id)).merged_into_customer_id is None
        assert db.scalar(select(CustomerMergeHistory)) is None


def test_merge_apply_rolls_back_links_history_and_marker_on_failure(
    auth_client: TestClient, make_user, login, db_factory, monkeypatch
) -> None:
    from travelos.core.audit import AuditWriter

    requester = make_user(email="merge-rollback-agent@example.test", role="agent")
    reviewer = make_user(email="merge-rollback-owner@example.test", role="owner", mfa_enrolled=True)
    requester_headers = bearer(token_for(auth_client, requester, login))
    reviewer_headers = bearer(token_for(auth_client, reviewer, login))

    def customer(name: str) -> str:
        return auth_client.post(
            "/crm/customers",
            json={"customer_type": "individual", "display_name": name},
            headers=requester_headers,
        ).json()["id"]

    source_id, target_id = (
        customer("Rollback Source Synthetic"),
        customer("Rollback Target Synthetic"),
    )
    contact = auth_client.post(
        f"/crm/customers/{source_id}/contacts",
        json={"channel": "email", "value": "rollback.merge@example.test"},
        headers=requester_headers,
    ).json()
    preview = auth_client.post(
        "/crm/merges/preview",
        json={"source_customer_id": source_id, "target_customer_id": target_id},
        headers=requester_headers,
    ).json()
    approval_id = preview["approval_id"]
    assert (
        auth_client.post(
            f"/approvals/{approval_id}/decision",
            json={"decision": "approve"},
            headers=reviewer_headers,
        ).status_code
        == 200
    )

    original_record = AuditWriter.record

    def fail_merge_audit(self, db, **kwargs):
        if kwargs.get("action") == "crm.customer_merge_applied":
            raise RuntimeError("synthetic audit failure")
        return original_record(self, db, **kwargs)

    monkeypatch.setattr(AuditWriter, "record", fail_merge_audit)
    with pytest.raises(RuntimeError, match="synthetic audit failure"):
        auth_client.post(f"/crm/merges/apply/{approval_id}", headers=requester_headers)
    with db_factory() as db:
        assert db.get(Customer, UUID(source_id)).merged_into_customer_id is None
        assert db.get(Contact, UUID(contact["id"])).customer_id == UUID(source_id)
        assert db.scalar(select(CustomerMergeHistory)) is None


def test_merge_undo_rejects_unsafe_new_target_primary(
    auth_client: TestClient, make_user, login, db_factory
) -> None:
    requester = make_user(email="merge-undo-stale-agent@example.test", role="agent")
    reviewer = make_user(
        email="merge-undo-stale-owner@example.test", role="owner", mfa_enrolled=True
    )
    requester_headers = bearer(token_for(auth_client, requester, login))
    reviewer_headers = bearer(token_for(auth_client, reviewer, login))

    def customer(name: str) -> str:
        return auth_client.post(
            "/crm/customers",
            json={"customer_type": "individual", "display_name": name},
            headers=requester_headers,
        ).json()["id"]

    source_id, target_id = customer("Undo Source Synthetic"), customer("Undo Target Synthetic")
    source_contact = auth_client.post(
        f"/crm/customers/{source_id}/contacts",
        json={"channel": "email", "value": "undo.source@example.test", "is_primary": True},
        headers=requester_headers,
    ).json()
    auth_client.post(
        f"/crm/customers/{target_id}/contacts",
        json={"channel": "phone", "value": "+919000000007", "is_primary": True},
        headers=requester_headers,
    )
    preview = auth_client.post(
        "/crm/merges/preview",
        json={"source_customer_id": source_id, "target_customer_id": target_id},
        headers=requester_headers,
    ).json()
    approval_id = preview["approval_id"]
    assert (
        auth_client.post(
            f"/approvals/{approval_id}/decision",
            json={"decision": "approve"},
            headers=reviewer_headers,
        ).status_code
        == 200
    )
    assert (
        auth_client.post(f"/crm/merges/apply/{approval_id}", headers=requester_headers).status_code
        == 200
    )

    added = auth_client.post(
        f"/crm/customers/{target_id}/contacts",
        json={"channel": "email", "value": "undo.new-primary@example.test", "is_primary": True},
        headers=requester_headers,
    )
    assert added.status_code == 201
    undo = auth_client.post(f"/crm/merges/{approval_id}/undo", headers=requester_headers)
    assert undo.status_code == 409
    missing = auth_client.post(
        "/crm/merges/00000000-0000-0000-0000-0000000000ff/undo",
        headers=requester_headers,
    )
    assert missing.status_code == 404
    with db_factory() as db:
        source = db.get(Customer, UUID(source_id))
        linked_contact = db.get(Contact, UUID(source_contact["id"]))
        assert source is not None and source.merged_into_customer_id == UUID(target_id)
        assert linked_contact is not None and linked_contact.customer_id == UUID(target_id)
