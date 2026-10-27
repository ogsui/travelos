"""Bootstrap user creation rules (no database)."""

from __future__ import annotations

import pytest

from travelos.admin.create_user import (
    CreateUserError,
    create_user,
    generate_password,
    normalise_email,
    validate_password,
    validate_role,
)
from travelos.core.config import Mode, Settings
from travelos.security.passwords import MIN_PASSWORD_LENGTH


def test_email_is_lowercased_and_validated() -> None:
    assert normalise_email("  Owner@Example.TEST ") == "owner@example.test"
    for bad in ("", "no-at-sign", "a" * 320 + "@example.test"):
        with pytest.raises(CreateUserError, match="invalid email"):
            normalise_email(bad)


def test_only_catalogue_roles_are_accepted() -> None:
    assert validate_role(" OWNER ") == "owner"
    for role in ("owner", "admin", "agent", "accountant"):
        assert validate_role(role) == role
    with pytest.raises(CreateUserError, match="unknown role"):
        validate_role("superuser")


def test_passwords_must_meet_the_minimum_length() -> None:
    assert validate_password("x" * MIN_PASSWORD_LENGTH)
    with pytest.raises(CreateUserError, match="at least"):
        validate_password("x" * (MIN_PASSWORD_LENGTH - 1))


def test_generated_passwords_are_long_and_use_the_allowed_alphabet() -> None:
    password = generate_password()
    assert len(password) == 20
    assert generate_password() != password
    assert set(password) <= set(
        "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789!@#$%^&*-_=+"
    )
    with pytest.raises(CreateUserError, match="at least"):
        generate_password(8)


def test_creation_is_refused_outside_test_mode() -> None:
    """No real staff accounts until the owner-approved onboarding flow exists."""

    shadow = Settings(_env_file=None, mode=Mode.SHADOW)
    with pytest.raises(CreateUserError, match="MODE=test"):
        create_user(shadow, email="owner@example.test", password="synthetic-password")
