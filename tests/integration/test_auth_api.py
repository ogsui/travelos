"""Auth API: login, MFA, sessions, CSRF and logout."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from travelos.core.models import AuthSession, User
from travelos.security.sessions import hash_token
from travelos.security.totp import generate_code

pytestmark = pytest.mark.integration


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def wrong_code(secret: str) -> str:
    candidate = "000000"
    return "111111" if generate_code(secret) == candidate else candidate


def login_ok(login, client: TestClient, user, *, code: str | None = None) -> str:
    if code is None and user.mfa_secret is not None:
        code = generate_code(user.mfa_secret)
    response = login(client, user, code=code)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def test_login_success_returns_a_hashed_session_and_audit_row(
    auth_client: TestClient,
    make_user,
    login,
    audit_rows,
    db_factory,
) -> None:
    user = make_user(email="agent@example.test", role="agent")

    response = login(auth_client, user)

    assert response.status_code == 200
    body = response.json()
    assert body["user"] == {
        "id": str(user.id),
        "email": "agent@example.test",
        "role": "agent",
        "is_active": True,
        "mfa_enrolled": False,
    }
    assert body["access_token"]
    assert body["csrf_token"]
    assert body["expires_at"]
    assert "password" not in json.dumps(body)
    assert "hash" not in json.dumps(body).lower()
    assert response.cookies.get("travelos_session") == body["access_token"]
    assert response.cookies.get("travelos_csrf") == body["csrf_token"]

    with db_factory() as db:
        stored = db.execute(select(AuthSession)).scalar_one()
        assert stored.token_hash == hash_token(body["access_token"])
        assert stored.token_hash != body["access_token"]
        assert stored.revoked_at is None

        refreshed = db.get(User, user.id)
        assert refreshed is not None
        assert refreshed.last_login_at is not None

    rows = audit_rows("auth.login")
    assert len(rows) == 1
    assert rows[0].actor_id == user.id
    assert rows[0].subject_id == user.id


def test_login_rejects_a_wrong_password_and_audits_it(
    auth_client: TestClient, make_user, login, audit_rows
) -> None:
    user = make_user()

    response = login(auth_client, user, password="not-the-password")

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_credentials"}
    rows = audit_rows("auth.login_failed")
    assert len(rows) == 1
    assert rows[0].actor_id == user.id
    assert rows[0].after == {"reason": "bad_password"}


def test_login_unknown_email_does_not_reveal_whether_the_account_exists(
    auth_client: TestClient, audit_rows
) -> None:
    response = auth_client.post(
        "/auth/login", json={"email": "nobody@example.test", "password": "whatever-123"}
    )

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_credentials"}
    rows = audit_rows("auth.login_failed")
    assert len(rows) == 1
    assert rows[0].actor_type == "system"
    assert rows[0].after == {"reason": "invalid_credentials"}


def test_inactive_user_cannot_log_in(auth_client: TestClient, make_user, login, audit_rows) -> None:
    user = make_user(is_active=False)

    response = login(auth_client, user)

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_credentials"}
    assert audit_rows("auth.login_failed")


def test_owner_without_mfa_enrollment_is_blocked(
    auth_client: TestClient, make_user, login, audit_rows
) -> None:
    user = make_user(role="owner")

    response = login(auth_client, user)

    assert response.status_code == 403
    assert response.json() == {"error": "mfa_enrollment_required"}
    rows = audit_rows("auth.mfa_enrollment_required")
    assert len(rows) == 1
    assert rows[0].after == {"reason": "role_requires_mfa"}


def test_admin_without_mfa_enrollment_is_blocked(auth_client: TestClient, make_user, login) -> None:
    user = make_user(role="admin")

    response = login(auth_client, user)

    assert response.status_code == 403
    assert response.json() == {"error": "mfa_enrollment_required"}


def test_mfa_code_is_required_when_the_user_has_a_secret(
    auth_client: TestClient, make_user, login, audit_rows
) -> None:
    user = make_user(role="owner", mfa_enrolled=True)

    response = login(auth_client, user)

    assert response.status_code == 401
    assert response.json() == {"error": "mfa_required"}
    rows = audit_rows("auth.mfa_failed")
    assert rows[0].after == {"reason": "code_missing"}


def test_invalid_mfa_code_is_rejected(
    auth_client: TestClient, make_user, login, audit_rows
) -> None:
    user = make_user(role="owner", mfa_enrolled=True)

    response = login(auth_client, user, code=wrong_code(user.mfa_secret))

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_mfa_code"}
    rows = audit_rows("auth.mfa_failed")
    assert rows[0].after == {"reason": "code_invalid"}


def test_valid_mfa_code_allows_login(auth_client: TestClient, make_user, login, audit_rows) -> None:
    user = make_user(role="owner", mfa_enrolled=True)

    token = login_ok(login, auth_client, user)

    assert token
    assert audit_rows("auth.login")


def test_agent_can_enroll_totp_then_login_with_it(
    auth_client: TestClient, make_user, login, audit_rows
) -> None:
    user = make_user(email="agent@example.test", role="agent")

    enrollment = auth_client.post(
        "/auth/mfa/enroll", json={"email": user.email, "password": user.password}
    )

    assert enrollment.status_code == 200
    body = enrollment.json()
    assert body["secret"]
    assert body["otpauth_uri"].startswith("otpauth://totp/")
    assert audit_rows("auth.mfa_enrolled")

    blocked = login(auth_client, user)
    assert blocked.status_code == 401
    assert blocked.json() == {"error": "mfa_required"}

    token = login_ok(login, auth_client, user, code=generate_code(body["secret"]))
    assert token


def test_mfa_enrollment_requires_the_password(
    auth_client: TestClient, make_user, audit_rows
) -> None:
    user = make_user(role="agent")

    response = auth_client.post(
        "/auth/mfa/enroll", json={"email": user.email, "password": "wrong-password-123"}
    )

    assert response.status_code == 401
    assert response.json() == {"error": "invalid_credentials"}
    rows = audit_rows("auth.mfa_enroll_failed")
    assert rows[0].after == {"reason": "invalid_credentials"}


def test_rotating_mfa_requires_a_valid_current_code(
    auth_client: TestClient, make_user, audit_rows
) -> None:
    user = make_user(role="owner", mfa_enrolled=True)
    old_secret = user.mfa_secret
    assert old_secret is not None

    blocked = auth_client.post(
        "/auth/mfa/enroll", json={"email": user.email, "password": user.password}
    )
    assert blocked.status_code == 401
    assert blocked.json() == {"error": "invalid_mfa_code"}

    rotated = auth_client.post(
        "/auth/mfa/enroll",
        json={
            "email": user.email,
            "password": user.password,
            "totp_code": generate_code(old_secret),
        },
    )

    assert rotated.status_code == 200
    new_secret = rotated.json()["secret"]
    assert new_secret != old_secret
    assert audit_rows("auth.mfa_rotated")

    follow_up = auth_client.post(
        "/auth/login",
        json={
            "email": user.email,
            "password": user.password,
            "totp_code": generate_code(new_secret),
        },
    )
    assert follow_up.status_code == 200


def test_me_requires_authentication_and_returns_the_caller(
    auth_client: TestClient, make_user, login
) -> None:
    anonymous = auth_client.get("/auth/me")
    assert anonymous.status_code == 401
    assert anonymous.json() == {"error": "authentication_required"}

    invalid = auth_client.get("/auth/me", headers=bearer("not-a-real-token"))
    assert invalid.status_code == 401
    assert invalid.json() == {"error": "invalid_session"}

    user = make_user(email="agent@example.test", role="agent")
    token = login_ok(login, auth_client, user)

    response = auth_client.get("/auth/me", headers=bearer(token))

    assert response.status_code == 200
    assert response.json()["email"] == user.email
    assert response.json()["role"] == "agent"
    assert response.json()["mfa_enrolled"] is False


def test_email_is_normalized_and_case_insensitive(
    auth_client: TestClient, make_user, login, audit_rows
) -> None:
    user = make_user(email="agent@example.test", role="agent")

    response = auth_client.post(
        "/auth/login",
        json={"email": "  AGENT@Example.Test ", "password": user.password},
    )

    assert response.status_code == 200
    assert response.json()["user"]["email"] == "agent@example.test"
    assert audit_rows("auth.login")


def test_logout_revokes_the_session(auth_client: TestClient, make_user, login, audit_rows) -> None:
    user = make_user(role="agent")
    token = login_ok(login, auth_client, user)

    logout = auth_client.post("/auth/logout", headers=bearer(token))

    assert logout.status_code == 204
    assert auth_client.get("/auth/me", headers=bearer(token)).status_code == 401
    assert audit_rows("auth.logout")


def test_logout_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.post("/auth/logout")

    assert response.status_code == 401
    assert response.json() == {"error": "authentication_required"}


def test_cookie_auth_requires_a_csrf_header_for_unsafe_methods(
    auth_client: TestClient, make_user, login, csrf_headers, audit_rows
) -> None:
    user = make_user(role="agent")
    login_ok(login, auth_client, user)

    # Cookie-only GET works.
    assert auth_client.get("/auth/me").status_code == 200

    blocked = auth_client.post("/auth/logout")
    assert blocked.status_code == 403
    assert blocked.json() == {"error": "csrf_failed"}
    assert audit_rows("auth.logout") == []

    allowed = auth_client.post("/auth/logout", headers=csrf_headers(auth_client))
    assert allowed.status_code == 204
    assert auth_client.get("/auth/me").status_code == 401
    assert len(audit_rows("auth.logout")) == 1


def test_bearer_auth_does_not_need_csrf(auth_client: TestClient, make_user, login) -> None:
    user = make_user(role="agent")
    token = login_ok(login, auth_client, user)

    response = auth_client.post("/auth/logout", headers=bearer(token))

    assert response.status_code == 204


def test_login_validation_rejects_missing_fields(auth_client: TestClient) -> None:
    response = auth_client.post("/auth/login", json={"email": "agent@example.test"})

    assert response.status_code == 422


def test_login_rejects_an_invalid_email(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/auth/login", json={"email": "not-an-email", "password": "synthetic-password-42"}
    )

    assert response.status_code == 422
