"""Roles and permissions.

Every protected route declares one permission. The matrix below is the single
place that decides which role may do what; it is deliberately small and
explicit so it can be reviewed line by line.

Locked rules (AGENTS.md, docs/05_APPROVAL_MATRIX.md): only the owner may change
kill switches or autonomy levels. Those are enforced here and again in the
ActionPolicy layer as it lands.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    OWNER = "owner"
    AGENT = "agent"
    ACCOUNTANT = "accountant"
    ADMIN = "admin"


class Permission(StrEnum):
    USERS_MANAGE = "users:manage"
    AUDIT_READ = "audit:read"
    FLAGS_READ = "flags:read"
    FLAGS_WRITE = "flags:write"
    CRM_READ = "crm:read"
    CRM_WRITE = "crm:write"
    QUOTES_READ = "quotes:read"
    QUOTES_WRITE = "quotes:write"
    BILLING_READ = "billing:read"
    BILLING_WRITE = "billing:write"
    REPORTS_READ = "reports:read"
    APPROVALS_READ = "approvals:read"
    APPROVALS_REQUEST = "approvals:request"
    APPROVALS_DECIDE = "approvals:decide"
    AUTONOMY_READ = "autonomy:read"
    AUTONOMY_WRITE = "autonomy:write"


MFA_REQUIRED_ROLES = frozenset({Role.OWNER, Role.ADMIN})

_OWNER_PERMISSIONS = frozenset(Permission)

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.OWNER: _OWNER_PERMISSIONS,
    Role.ADMIN: frozenset(
        {
            Permission.USERS_MANAGE,
            Permission.AUDIT_READ,
            Permission.FLAGS_READ,
            Permission.CRM_READ,
            Permission.APPROVALS_READ,
            Permission.AUTONOMY_READ,
        }
    ),
    Role.AGENT: frozenset(
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
    ),
    Role.ACCOUNTANT: frozenset(
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
    ),
}


def permissions_for(role: str | Role | None) -> frozenset[Permission]:
    """Permissions for a role. Unknown or missing roles get nothing."""

    if role is None:
        return frozenset()
    try:
        parsed = role if isinstance(role, Role) else Role(role)
    except ValueError:
        return frozenset()
    return ROLE_PERMISSIONS.get(parsed, frozenset())


def has_permission(role: str | Role | None, permission: Permission) -> bool:
    return permission in permissions_for(role)


def requires_mfa(role: str | Role | None) -> bool:
    """Owner and admin accounts must have TOTP enabled before they can log in."""

    try:
        parsed = role if isinstance(role, Role) else Role(role)  # type: ignore[arg-type]
    except ValueError:
        return False
    return parsed in MFA_REQUIRED_ROLES
