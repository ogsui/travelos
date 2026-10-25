"""Every API route is covered by an allowed + denied (or public) access test."""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from travelos.security.permissions import Role
from travelos.security.totp import generate_code

pytestmark = pytest.mark.integration

ALL_ROLES = [role.value for role in Role]
MFA_ROLES = {"owner", "admin"}

# The single registry the completeness test enforces. Adding a route without
# updating this map (and its access tests) fails the suite.
EXPECTED_ROUTES = {
    ("GET", "/health"),
    ("GET", "/ready"),
    ("POST", "/auth/login"),
    ("POST", "/auth/logout"),
    ("GET", "/auth/me"),
    ("POST", "/auth/mfa/enroll"),
    ("GET", "/admin/flags"),
    ("PUT", "/admin/flags/{key}"),
    ("POST", "/crm/consents"),
    ("POST", "/crm/consents/{consent_id}/withdraw"),
    ("GET", "/crm/contacts/{contact_id}/can-send"),
    ("POST", "/crm/customers"),
    ("GET", "/crm/customers"),
    ("GET", "/crm/customers/{customer_id}"),
    ("POST", "/crm/customers/{customer_id}/contacts"),
    ("GET", "/crm/customers/{customer_id}/contacts"),
    ("POST", "/crm/customers/{customer_id}/travellers"),
    ("GET", "/crm/customers/{customer_id}/travellers"),
    ("POST", "/crm/imports/preview"),
    ("POST", "/crm/imports/apply/{approval_id}"),
    ("POST", "/crm/merges/preview"),
    ("POST", "/crm/merges/apply/{approval_id}"),
    ("POST", "/approvals"),
    ("GET", "/approvals"),
    ("GET", "/approvals/{request_id}"),
    ("POST", "/approvals/{request_id}/decision"),
    ("GET", "/autonomy/policies"),
    ("PUT", "/autonomy/policies/{action_type}"),
}


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login_as(login, client: TestClient, make_user, role: str, email: str | None = None) -> str:
    user = make_user(
        email=email or f"{role}@example.test",
        role=role,
        mfa_enrolled=role in MFA_ROLES,
    )
    code = generate_code(user.mfa_secret) if user.mfa_secret else None
    response = login(client, user, code=code)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def api_route_pairs(app: FastAPI) -> set[tuple[str, str]]:
    """All documented operations, read from the generated OpenAPI schema.

    FastAPI wraps included routers, so walking ``app.routes`` is version
    dependent; the schema is the stable public surface.
    """

    schema = app.openapi()
    return {
        (method.upper(), path)
        for path, operations in schema["paths"].items()
        for method in operations
    }


def test_every_route_is_registered_in_the_access_plan(auth_app: FastAPI) -> None:
    assert api_route_pairs(auth_app) == EXPECTED_ROUTES


@pytest.mark.parametrize(("method", "path"), [("GET", "/health"), ("GET", "/ready")])
def test_health_routes_are_public(auth_client: TestClient, method: str, path: str) -> None:
    assert auth_client.request(method, path).status_code == 200


@pytest.mark.parametrize("path", ["/auth/login", "/auth/mfa/enroll"])
def test_login_and_enrollment_are_public_and_check_credentials(
    auth_client: TestClient, path: str
) -> None:
    response = auth_client.post(
        path, json={"email": "nobody@example.test", "password": "synthetic-password-42"}
    )

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_credentials"}


@pytest.mark.parametrize("role", ALL_ROLES)
def test_me_allows_every_role(auth_client: TestClient, make_user, login, role: str) -> None:
    token = login_as(login, auth_client, make_user, role)

    response = auth_client.get("/auth/me", headers=bearer(token))

    assert response.status_code == 200
    assert response.json()["role"] == role


@pytest.mark.parametrize("role", ALL_ROLES)
def test_logout_allows_every_role(auth_client: TestClient, make_user, login, role: str) -> None:
    token = login_as(login, auth_client, make_user, role)

    response = auth_client.post("/auth/logout", headers=bearer(token))

    assert response.status_code == 204


@pytest.mark.parametrize("role", ["owner", "admin"])
def test_flag_read_allows_owner_and_admin(
    auth_client: TestClient, make_user, login, role: str
) -> None:
    token = login_as(login, auth_client, make_user, role)

    response = auth_client.get("/admin/flags", headers=bearer(token))

    assert response.status_code == 200


@pytest.mark.parametrize("role", ["agent", "accountant"])
def test_flag_read_denies_agent_and_accountant(
    auth_client: TestClient, make_user, login, audit_rows, role: str
) -> None:
    token = login_as(login, auth_client, make_user, role)

    response = auth_client.get("/admin/flags", headers=bearer(token))

    assert response.status_code == 403
    assert audit_rows("auth.permission_denied")


@pytest.mark.parametrize("role", ["admin", "agent", "accountant"])
def test_flag_write_denies_every_role_but_owner(
    permissive_client: TestClient, make_user, login, audit_rows, role: str
) -> None:
    token = login_as(login, permissive_client, make_user, role)

    response = permissive_client.put(
        "/admin/flags/email", json={"enabled": True}, headers=bearer(token)
    )

    assert response.status_code == 403
    assert audit_rows("auth.permission_denied")


def test_flag_write_allows_owner(permissive_client: TestClient, make_user, login) -> None:
    token = login_as(login, permissive_client, make_user, "owner")

    response = permissive_client.put(
        "/admin/flags/email", json={"enabled": True}, headers=bearer(token)
    )

    assert response.status_code == 200
    assert response.json()["source"] == "database"


def test_crm_write_denies_admin(auth_client: TestClient, make_user, login, audit_rows) -> None:
    token = login_as(login, auth_client, make_user, "admin")

    response = auth_client.post(
        "/crm/consents",
        json={
            "contact_id": "00000000-0000-0000-0000-000000000001",
            "channel": "email",
            "purpose": "marketing",
            "source": "web_form",
        },
        headers=bearer(token),
    )

    assert response.status_code == 403
    merge_preview = auth_client.post(
        "/crm/merges/preview",
        json={
            "source_customer_id": "00000000-0000-0000-0000-0000000000aa",
            "target_customer_id": "00000000-0000-0000-0000-0000000000bb",
        },
        headers=bearer(token),
    )
    assert merge_preview.status_code == 403
    assert (
        auth_client.post(
            "/crm/merges/apply/00000000-0000-0000-0000-0000000000aa", headers=bearer(token)
        ).status_code
        == 403
    )
    assert audit_rows("auth.permission_denied")


def test_crm_read_denies_accountant(auth_client: TestClient, make_user, login, audit_rows) -> None:
    token = login_as(login, auth_client, make_user, "accountant")

    response = auth_client.get(
        "/crm/contacts/00000000-0000-0000-0000-000000000001/can-send",
        params={"channel": "email", "purpose": "marketing"},
        headers=bearer(token),
    )

    assert response.status_code == 403
    assert audit_rows("auth.permission_denied")


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/auth/me"),
        ("POST", "/auth/logout"),
        ("GET", "/admin/flags"),
        ("PUT", "/admin/flags/email"),
        ("POST", "/crm/consents"),
        ("POST", "/crm/consents/00000000-0000-0000-0000-0000000000aa/withdraw"),
        ("GET", "/crm/contacts/00000000-0000-0000-0000-0000000000aa/can-send"),
        ("POST", "/crm/customers"),
        ("GET", "/crm/customers"),
        ("GET", "/crm/customers/00000000-0000-0000-0000-0000000000aa"),
        ("POST", "/crm/customers/00000000-0000-0000-0000-0000000000aa/contacts"),
        ("GET", "/crm/customers/00000000-0000-0000-0000-0000000000aa/contacts"),
        ("POST", "/crm/customers/00000000-0000-0000-0000-0000000000aa/travellers"),
        ("GET", "/crm/customers/00000000-0000-0000-0000-0000000000aa/travellers"),
        ("POST", "/crm/imports/preview"),
        ("POST", "/crm/imports/apply/00000000-0000-0000-0000-0000000000aa"),
        ("POST", "/crm/merges/preview"),
        ("POST", "/crm/merges/apply/00000000-0000-0000-0000-0000000000aa"),
        ("GET", "/approvals"),
        ("POST", "/approvals"),
        ("GET", "/approvals/00000000-0000-0000-0000-0000000000aa"),
        ("POST", "/approvals/00000000-0000-0000-0000-0000000000aa/decision"),
        ("GET", "/autonomy/policies"),
        ("PUT", "/autonomy/policies/quote.send"),
    ],
)
def test_protected_routes_reject_anonymous_callers(
    auth_client: TestClient, method: str, path: str
) -> None:
    response = auth_client.request(method, path, json={"enabled": True})

    assert response.status_code == 401


# --------------------------------------------------------------------------
# Approvals and autonomy
# --------------------------------------------------------------------------

APPROVAL_PAYLOAD = {
    "action_type": "quote.send",
    "subject_type": "quote",
    "proposed": {"total_paise": 1},
}


def submit_ticket(client: TestClient, token: str) -> str:
    response = client.post("/approvals", json=APPROVAL_PAYLOAD, headers=bearer(token))
    assert response.status_code == 200, response.text
    return str(response.json()["approval"]["id"])


@pytest.mark.parametrize("role", ALL_ROLES)
def test_approval_read_allows_every_role(
    auth_client: TestClient, make_user, login, role: str
) -> None:
    token = login_as(login, auth_client, make_user, role)

    assert auth_client.get("/approvals", headers=bearer(token)).status_code == 200


@pytest.mark.parametrize("role", ["owner", "agent", "accountant"])
def test_approval_request_allows_operators(
    auth_client: TestClient, make_user, login, role: str
) -> None:
    token = login_as(login, auth_client, make_user, role)

    response = auth_client.post("/approvals", json=APPROVAL_PAYLOAD, headers=bearer(token))

    assert response.status_code == 200, response.text
    assert response.json()["decision"]["decision"] == "needs_approval"


def test_approval_request_denies_admin(
    auth_client: TestClient, make_user, login, audit_rows
) -> None:
    token = login_as(login, auth_client, make_user, "admin")

    response = auth_client.post("/approvals", json=APPROVAL_PAYLOAD, headers=bearer(token))

    assert response.status_code == 403
    assert audit_rows("auth.permission_denied")


@pytest.mark.parametrize("role", ["owner", "agent", "accountant"])
def test_approval_decision_allows_operators(
    auth_client: TestClient, make_user, login, role: str
) -> None:
    requester_token = login_as(login, auth_client, make_user, "agent", "requester@example.test")
    request_id = submit_ticket(auth_client, requester_token)
    token = login_as(login, auth_client, make_user, role, f"{role}@example.test")

    response = auth_client.post(
        f"/approvals/{request_id}/decision",
        json={"decision": "approve"},
        headers=bearer(token),
    )

    assert response.status_code == 200, response.text


def test_approval_decision_denies_admin(
    auth_client: TestClient, make_user, login, audit_rows
) -> None:
    requester_token = login_as(login, auth_client, make_user, "agent", "requester@example.test")
    request_id = submit_ticket(auth_client, requester_token)
    token = login_as(login, auth_client, make_user, "admin", "admin@example.test")

    response = auth_client.post(
        f"/approvals/{request_id}/decision",
        json={"decision": "approve"},
        headers=bearer(token),
    )

    assert response.status_code == 403
    assert audit_rows("auth.permission_denied")


@pytest.mark.parametrize("role", ALL_ROLES)
def test_autonomy_read_allows_every_role(
    auth_client: TestClient, make_user, login, role: str
) -> None:
    token = login_as(login, auth_client, make_user, role)

    assert auth_client.get("/autonomy/policies", headers=bearer(token)).status_code == 200


@pytest.mark.parametrize("role", ["admin", "agent", "accountant"])
def test_autonomy_write_denies_every_role_but_owner(
    auth_client: TestClient, make_user, login, audit_rows, role: str
) -> None:
    token = login_as(login, auth_client, make_user, role)

    response = auth_client.put(
        "/autonomy/policies/quote.send",
        json={"level": "A2"},
        headers=bearer(token),
    )

    assert response.status_code == 403
    assert audit_rows("auth.permission_denied")


def test_autonomy_write_allows_owner(auth_client: TestClient, make_user, login) -> None:
    token = login_as(login, auth_client, make_user, "owner")

    response = auth_client.put(
        "/autonomy/policies/quote.send",
        json={"level": "A2"},
        headers=bearer(token),
    )

    assert response.status_code == 200
    assert response.json()["level"] == "A2"
