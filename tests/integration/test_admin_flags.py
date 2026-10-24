"""Owner-only kill-switch endpoints: RBAC, audit, precedence and cache behaviour."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from travelos.core.config import KillSwitches
from travelos.core.models import FeatureFlag
from travelos.security.totp import generate_code

pytestmark = pytest.mark.integration


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def login_ok(login, client: TestClient, user) -> str:
    code = generate_code(user.mfa_secret) if user.mfa_secret else None
    response = login(client, user, code=code)
    assert response.status_code == 200, response.text
    return response.json()["access_token"]


def owner(make_user):
    return make_user(email="owner@example.test", role="owner", mfa_enrolled=True)


def test_flag_listing_requires_authentication(auth_client: TestClient) -> None:
    response = auth_client.get("/admin/flags")

    assert response.status_code == 401
    assert response.json() == {"error": "authentication_required"}


def test_agent_cannot_list_flags_and_the_denial_is_audited(
    auth_client: TestClient, make_user, login, audit_rows
) -> None:
    user = make_user(role="agent")
    token = login_ok(login, auth_client, user)

    response = auth_client.get("/admin/flags", headers=bearer(token))

    assert response.status_code == 403
    assert response.json() == {"error": "permission_denied"}
    rows = audit_rows("auth.permission_denied")
    assert len(rows) == 1
    assert rows[0].actor_id == user.id
    assert rows[0].after == {
        "method": "GET",
        "path": "/admin/flags",
        "permissions": ["flags:read"],
    }


def test_accountant_cannot_list_flags(
    auth_client: TestClient, make_user, login, audit_rows
) -> None:
    user = make_user(role="accountant")
    token = login_ok(login, auth_client, user)

    response = auth_client.get("/admin/flags", headers=bearer(token))

    assert response.status_code == 403
    assert audit_rows("auth.permission_denied")


def test_admin_can_read_flags_but_not_change_them(
    auth_client: TestClient, make_user, login, audit_rows
) -> None:
    user = make_user(role="admin", mfa_enrolled=True)
    token = login_ok(login, auth_client, user)

    listing = auth_client.get("/admin/flags", headers=bearer(token))
    assert listing.status_code == 200

    change = auth_client.put("/admin/flags/email", json={"enabled": True}, headers=bearer(token))
    assert change.status_code == 403
    assert change.json() == {"error": "permission_denied"}
    rows = audit_rows("auth.permission_denied")
    assert rows[0].after["permissions"] == ["flags:write"]


def test_owner_lists_every_flag_off_by_default(auth_client: TestClient, make_user, login) -> None:
    token = login_ok(login, auth_client, owner(make_user))

    response = auth_client.get("/admin/flags", headers=bearer(token))

    assert response.status_code == 200
    flags = response.json()
    assert {flag["key"] for flag in flags} == set(KillSwitches.switch_names())
    assert all(flag["enabled"] is False for flag in flags)
    assert all(flag["source"] == "operator_kill_switch" for flag in flags)


def test_owner_can_enable_a_flag_and_the_change_is_audited(
    permissive_client: TestClient,
    make_user,
    login,
    audit_rows,
    db_factory,
) -> None:
    user = owner(make_user)
    token = login_ok(login, permissive_client, user)

    response = permissive_client.put(
        "/admin/flags/email", json={"enabled": True}, headers=bearer(token)
    )

    assert response.status_code == 200
    assert response.json() == {
        "key": "email",
        "enabled": True,
        "source": "database",
        "scope": "global",
    }

    with db_factory() as db:
        row = db.execute(select(FeatureFlag).where(FeatureFlag.key == "email")).scalar_one()
        assert row.value == {"enabled": True}
        assert row.updated_by == user.id
        row_id = row.id

    rows = audit_rows("flags.updated")
    assert len(rows) == 1
    assert rows[0].actor_id == user.id
    assert rows[0].subject_type == "feature_flag"
    assert rows[0].subject_id == row_id
    assert rows[0].after == {"enabled": True}


def test_owner_can_disable_a_flag_again(
    permissive_client: TestClient, make_user, login, audit_rows
) -> None:
    token = login_ok(login, permissive_client, owner(make_user))
    permissive_client.put("/admin/flags/email", json={"enabled": True}, headers=bearer(token))

    response = permissive_client.put(
        "/admin/flags/email", json={"enabled": False}, headers=bearer(token)
    )

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    rows = audit_rows("flags.updated")
    assert len(rows) == 2
    assert rows[-1].before == {"enabled": True}
    assert rows[-1].after == {"enabled": False}


def test_operator_kill_switch_cannot_be_overridden_from_the_api(
    auth_client: TestClient, make_user, login, audit_rows, db_factory
) -> None:
    token = login_ok(login, auth_client, owner(make_user))

    response = auth_client.put("/admin/flags/email", json={"enabled": True}, headers=bearer(token))

    assert response.status_code == 200
    assert response.json()["enabled"] is False
    assert response.json()["source"] == "operator_kill_switch"
    # The owner-level intent is still stored and audited; only the operator can
    # make the capability available in this deployment.
    with db_factory() as db:
        row = db.execute(select(FeatureFlag).where(FeatureFlag.key == "email")).scalar_one()
        assert row.value == {"enabled": True}
    assert audit_rows("flags.updated")


def test_unknown_flag_is_rejected(permissive_client: TestClient, make_user, login) -> None:
    token = login_ok(login, permissive_client, owner(make_user))

    response = permissive_client.put(
        "/admin/flags/telepathy", json={"enabled": True}, headers=bearer(token)
    )

    assert response.status_code == 404
    assert response.json() == {"error": "unknown_kill_switch"}


def test_flag_values_must_be_booleans(permissive_client: TestClient, make_user, login) -> None:
    token = login_ok(login, permissive_client, owner(make_user))

    response = permissive_client.put(
        "/admin/flags/email", json={"enabled": "yes"}, headers=bearer(token)
    )

    assert response.status_code == 422


def test_flag_change_invalidates_the_read_cache(
    permissive_client: TestClient, make_user, login, db_factory
) -> None:
    token = login_ok(login, permissive_client, owner(make_user))

    enabled = permissive_client.put(
        "/admin/flags/email", json={"enabled": True}, headers=bearer(token)
    )
    assert enabled.json()["enabled"] is True

    def email_flag() -> dict[str, object]:
        flags = permissive_client.get("/admin/flags", headers=bearer(token)).json()
        return next(flag for flag in flags if flag["key"] == "email")

    assert email_flag()["enabled"] is True

    # Change the row behind the API's back. The cached read must not notice.
    with db_factory() as db:
        row = db.execute(select(FeatureFlag).where(FeatureFlag.key == "email")).scalar_one()
        row.value = {"enabled": False}
        db.commit()

    assert email_flag()["enabled"] is True

    # A write through the API invalidates the cache, so the change is visible.
    disabled = permissive_client.put(
        "/admin/flags/email", json={"enabled": False}, headers=bearer(token)
    )
    assert disabled.json()["enabled"] is False
    assert email_flag()["enabled"] is False
