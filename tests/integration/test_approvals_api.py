"""Approval and autonomy API: RBAC, resolve/submit, decide and locked policies."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from travelos.security.permissions import Role
from travelos.security.totp import generate_code

pytestmark = pytest.mark.integration

MFA_ROLES = {"owner", "admin"}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login_as(login, client: TestClient, make_user, role: str, email: str | None = None) -> str:
    user = make_user(
        email=email or f"{role}-{Role.OWNER.value}@example.test".replace("owner", role),
        role=role,
        mfa_enrolled=role in MFA_ROLES,
    )
    code = generate_code(user.mfa_secret) if user.mfa_secret else None
    response = login(client, user, code=code)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def submit_payload(action_type: str = "quote.send") -> dict[str, object]:
    return {
        "action_type": action_type,
        "subject_type": "quote",
        "subject_id": "00000000-0000-0000-0000-0000000000aa",
        "proposed": {"total_paise": 12_500_000, "currency": "INR"},
        "reason": "synthetic quote for review",
    }


def test_agent_submits_and_owner_decides(auth_client: TestClient, make_user, login) -> None:
    agent_token = login_as(login, auth_client, make_user, "agent", "agent@example.test")
    owner_token = login_as(login, auth_client, make_user, "owner", "owner@example.test")

    submitted = auth_client.post("/approvals", json=submit_payload(), headers=bearer(agent_token))
    assert submitted.status_code == 200, submitted.text
    body = submitted.json()
    assert body["decision"]["decision"] == "needs_approval"
    assert body["decision"]["level"] == "A1"
    assert body["approval"]["status"] == "pending"
    request_id = body["approval"]["id"]

    listed = auth_client.get("/approvals?status=pending", headers=bearer(owner_token))
    assert listed.status_code == 200
    assert [row["id"] for row in listed.json()] == [request_id]

    fetched = auth_client.get(f"/approvals/{request_id}", headers=bearer(agent_token))
    assert fetched.status_code == 200
    assert fetched.json()["id"] == request_id

    decided = auth_client.post(
        f"/approvals/{request_id}/decision",
        json={"decision": "approve"},
        headers=bearer(owner_token),
    )
    assert decided.status_code == 200, decided.text
    assert decided.json()["status"] == "approved"
    assert decided.json()["final"] == {"total_paise": 12_500_000, "currency": "INR"}


def test_auto_action_returns_auto_and_no_request(auth_client: TestClient, make_user, login) -> None:
    agent_token = login_as(login, auth_client, make_user, "agent")
    payload = submit_payload("crm.capture_lead")

    response = auth_client.post("/approvals", json=payload, headers=bearer(agent_token))

    assert response.status_code == 200
    assert response.json()["decision"]["decision"] == "auto"
    assert response.json()["approval"] is None


def test_a0_action_returns_403(auth_client: TestClient, make_user, login) -> None:
    agent_token = login_as(login, auth_client, make_user, "agent")
    payload = submit_payload("payment.supplier_payment")

    response = auth_client.post("/approvals", json=payload, headers=bearer(agent_token))

    assert response.status_code == 403
    assert response.json() == {"error": "autonomy_denied"}


def test_rejection_requires_a_reason_over_the_api(
    auth_client: TestClient, make_user, login
) -> None:
    agent_token = login_as(login, auth_client, make_user, "agent", "a@example.test")
    owner_token = login_as(login, auth_client, make_user, "owner", "o@example.test")
    request_id = auth_client.post(
        "/approvals", json=submit_payload(), headers=bearer(agent_token)
    ).json()["approval"]["id"]

    missing = auth_client.post(
        f"/approvals/{request_id}/decision",
        json={"decision": "reject"},
        headers=bearer(owner_token),
    )
    assert missing.status_code == 422
    assert missing.json() == {"error": "reason_required"}

    rejected = auth_client.post(
        f"/approvals/{request_id}/decision",
        json={"decision": "reject", "reason": "customer changed dates"},
        headers=bearer(owner_token),
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["reason"] == "customer changed dates"


def test_self_approval_is_refused_over_the_api(auth_client: TestClient, make_user, login) -> None:
    token = login_as(login, auth_client, make_user, "agent")
    request_id = auth_client.post(
        "/approvals", json=submit_payload(), headers=bearer(token)
    ).json()["approval"]["id"]

    response = auth_client.post(
        f"/approvals/{request_id}/decision",
        json={"decision": "approve"},
        headers=bearer(token),
    )

    assert response.status_code == 403
    assert response.json() == {"error": "self_approval_forbidden"}


def test_agent_cannot_write_autonomy(auth_client: TestClient, make_user, login, audit_rows) -> None:
    token = login_as(login, auth_client, make_user, "agent")

    response = auth_client.put(
        "/autonomy/policies/quote.send",
        json={"level": "A2"},
        headers=bearer(token),
    )

    assert response.status_code == 403
    assert audit_rows("auth.permission_denied")


def test_owner_can_lower_a_policy_and_locked_actions_stay_locked(
    auth_client: TestClient, make_user, login
) -> None:
    token = login_as(login, auth_client, make_user, "owner")

    policies = auth_client.get("/autonomy/policies", headers=bearer(token))
    assert policies.status_code == 200
    assert len(policies.json()) >= 45

    updated = auth_client.put(
        "/autonomy/policies/quote.send",
        json={"level": "A2", "value_threshold_paise": 0},
        headers=bearer(token),
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["level"] == "A2"
    assert updated.json()["source"] == "database"

    locked = auth_client.put(
        "/autonomy/policies/booking.confirm",
        json={"level": "A3"},
        headers=bearer(token),
    )
    assert locked.status_code == 409
    assert locked.json() == {"error": "policy_locked"}


def test_owner_cannot_raise_a_non_locked_action_above_its_ceiling(
    auth_client: TestClient, make_user, login
) -> None:
    token = login_as(login, auth_client, make_user, "owner")

    response = auth_client.put(
        "/autonomy/policies/quote.send",
        json={"level": "A3"},
        headers=bearer(token),
    )

    assert response.status_code == 409
    assert response.json() == {"error": "policy_locked"}


def test_approval_routes_reject_anonymous_callers(auth_client: TestClient) -> None:
    assert auth_client.get("/approvals").status_code == 401
    assert auth_client.get("/autonomy/policies").status_code == 401
    assert auth_client.post("/approvals", json=submit_payload()).status_code == 401
