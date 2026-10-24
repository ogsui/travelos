"""Argon2 password hashing tests."""

from __future__ import annotations

import pytest

from travelos.security.passwords import (
    hash_password,
    needs_rehash,
    verify_password,
    verify_password_or_dummy,
)


def test_hash_is_argon2id_and_verifies() -> None:
    password_hash = hash_password("synthetic-password-42")

    assert password_hash.startswith("$argon2id$")
    assert "synthetic-password-42" not in password_hash
    assert verify_password("synthetic-password-42", password_hash) is True
    assert verify_password("wrong-password", password_hash) is False


def test_same_password_gets_a_new_salt() -> None:
    first = hash_password("synthetic-password-42")
    second = hash_password("synthetic-password-42")

    assert first != second
    assert verify_password("synthetic-password-42", first)
    assert verify_password("synthetic-password-42", second)


def test_fresh_hash_does_not_need_rehash() -> None:
    assert needs_rehash(hash_password("synthetic-password-42")) is False


def test_malformed_hash_needs_rehash_and_never_verifies() -> None:
    assert needs_rehash("not-a-hash") is True
    assert verify_password("anything", "not-a-hash") is False


@pytest.mark.parametrize("value", ["", None])
def test_empty_password_is_rejected_for_hashing(value: str | None) -> None:
    with pytest.raises(ValueError, match="non-empty"):
        hash_password(value)  # type: ignore[arg-type]


def test_unknown_user_still_spends_verification_time() -> None:
    assert verify_password_or_dummy("synthetic-password-42", None) is False


def test_dummy_path_and_real_path_agree_on_valid_password() -> None:
    password_hash = hash_password("synthetic-password-42")

    assert verify_password_or_dummy("synthetic-password-42", password_hash) is True
    assert verify_password_or_dummy("wrong-password", password_hash) is False
