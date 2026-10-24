"""RBAC matrix tests, including the locked owner-only permissions."""

from __future__ import annotations

import pytest

from travelos.security.permissions import (
    MFA_REQUIRED_ROLES,
    ROLE_PERMISSIONS,
    Permission,
    Role,
    has_permission,
    permissions_for,
    requires_mfa,
)


def test_every_role_has_an_explicit_matrix_entry() -> None:
    assert set(ROLE_PERMISSIONS) == set(Role)


def test_owner_has_every_permission() -> None:
    assert permissions_for(Role.OWNER) == frozenset(Permission)


@pytest.mark.parametrize("permission", list(Permission))
def test_owner_has_every_permission_including_flag_writes(permission: Permission) -> None:
    assert has_permission(Role.OWNER, permission)


def test_flags_write_is_owner_only() -> None:
    assert [role for role in Role if has_permission(role, Permission.FLAGS_WRITE)] == [Role.OWNER]


def test_autonomy_write_is_owner_only() -> None:
    assert [role for role in Role if has_permission(role, Permission.AUTONOMY_WRITE)] == [
        Role.OWNER
    ]


def test_every_role_can_read_approvals_and_autonomy() -> None:
    for role in Role:
        assert has_permission(role, Permission.APPROVALS_READ), role
        assert has_permission(role, Permission.AUTONOMY_READ), role


def test_agents_and_accountants_request_and_decide_approvals() -> None:
    for role in (Role.AGENT, Role.ACCOUNTANT):
        assert has_permission(role, Permission.APPROVALS_REQUEST), role
        assert has_permission(role, Permission.APPROVALS_DECIDE), role


def test_admin_can_read_but_not_request_or_decide_approvals() -> None:
    assert has_permission(Role.ADMIN, Permission.APPROVALS_READ) is True
    assert has_permission(Role.ADMIN, Permission.APPROVALS_REQUEST) is False
    assert has_permission(Role.ADMIN, Permission.APPROVALS_DECIDE) is False
    assert has_permission(Role.ADMIN, Permission.AUTONOMY_WRITE) is False


def test_admin_can_read_flags_but_not_change_them() -> None:
    assert has_permission(Role.ADMIN, Permission.FLAGS_READ) is True
    assert has_permission(Role.ADMIN, Permission.FLAGS_WRITE) is False


def test_admin_can_manage_users_and_read_audit() -> None:
    assert has_permission(Role.ADMIN, Permission.USERS_MANAGE) is True
    assert has_permission(Role.ADMIN, Permission.AUDIT_READ) is True


def test_agent_is_limited_to_crm_quotes_and_approvals() -> None:
    assert permissions_for(Role.AGENT) == frozenset(
        {
            Permission.CRM_READ,
            Permission.CRM_WRITE,
            Permission.QUOTES_READ,
            Permission.QUOTES_WRITE,
            Permission.APPROVALS_READ,
            Permission.APPROVALS_REQUEST,
            Permission.APPROVALS_DECIDE,
            Permission.AUTONOMY_READ,
        }
    )


def test_accountant_is_limited_to_billing_quotes_reports_and_approvals() -> None:
    assert permissions_for(Role.ACCOUNTANT) == frozenset(
        {
            Permission.BILLING_READ,
            Permission.BILLING_WRITE,
            Permission.QUOTES_READ,
            Permission.REPORTS_READ,
            Permission.APPROVALS_READ,
            Permission.APPROVALS_REQUEST,
            Permission.APPROVALS_DECIDE,
            Permission.AUTONOMY_READ,
        }
    )


def test_agents_and_accountants_cannot_read_audit_or_flags() -> None:
    for role in (Role.AGENT, Role.ACCOUNTANT):
        assert has_permission(role, Permission.AUDIT_READ) is False
        assert has_permission(role, Permission.FLAGS_READ) is False
        assert has_permission(role, Permission.USERS_MANAGE) is False


@pytest.mark.parametrize("role", [None, "", "intern", "OWNER"])
def test_unknown_or_missing_roles_get_no_permissions(role: str | None) -> None:
    assert permissions_for(role) == frozenset()
    assert has_permission(role, Permission.CRM_READ) is False


def test_role_values_round_trip_from_strings() -> None:
    assert permissions_for("owner") == permissions_for(Role.OWNER)
    assert has_permission("admin", Permission.FLAGS_READ)


def test_mfa_is_required_only_for_owner_and_admin() -> None:
    assert {Role.OWNER, Role.ADMIN} == MFA_REQUIRED_ROLES
    assert requires_mfa(Role.OWNER) is True
    assert requires_mfa(Role.ADMIN) is True
    assert requires_mfa("owner") is True
    assert requires_mfa(Role.AGENT) is False
    assert requires_mfa(Role.ACCOUNTANT) is False
    assert requires_mfa(None) is False
    assert requires_mfa("intern") is False
