"""Field-level encryption: round-trip, tamper detection, key handling, log safety."""

from __future__ import annotations

import base64
import json
import logging
from datetime import date
from io import StringIO

import pytest
from pydantic import SecretStr

from travelos.core.config import get_settings, reset_settings_cache
from travelos.core.errors import EncryptionError, EncryptionKeyMissingError
from travelos.core.logging import JsonFormatter
from travelos.core.redaction import RedactionFilter, redact_text
from travelos.security.encryption import (
    ENCRYPTED_PREFIX,
    EncryptionService,
    SettingsKeyProvider,
    StaticKeyProvider,
    default_key_provider,
    generate_key,
    set_key_provider,
)

TEST_KEY = b"0123456789abcdef0123456789abcdef"


def service() -> EncryptionService:
    return EncryptionService(StaticKeyProvider(TEST_KEY))


def test_roundtrip_preserves_unicode_text() -> None:
    plaintext = "प्रिया शर्मा — passport holder"

    ciphertext = service().encrypt(plaintext)

    assert ciphertext.startswith(ENCRYPTED_PREFIX)
    assert plaintext not in ciphertext
    assert service().decrypt(ciphertext) == plaintext


def test_same_plaintext_produces_different_ciphertext() -> None:
    svc = service()

    assert svc.encrypt("+919876543210") != svc.encrypt("+919876543210")


def test_tampered_ciphertext_is_rejected() -> None:
    ciphertext = service().encrypt("M1234567")
    tampered = ciphertext[:-4] + ("AAAA" if not ciphertext.endswith("AAAA") else "BBBB")

    with pytest.raises(EncryptionError, match="could not be decrypted"):
        service().decrypt(tampered)


def test_plaintext_value_is_not_accepted_as_ciphertext() -> None:
    with pytest.raises(EncryptionError, match="not TravelOS ciphertext"):
        service().decrypt("M1234567")


def test_wrong_key_cannot_decrypt() -> None:
    ciphertext = service().encrypt("secret")

    with pytest.raises(EncryptionError):
        EncryptionService(StaticKeyProvider(b"x" * 32)).decrypt(ciphertext)


def test_static_provider_rejects_wrong_key_length() -> None:
    with pytest.raises(EncryptionError, match="32 bytes"):
        StaticKeyProvider(b"short")


def test_generate_key_is_valid_base64() -> None:
    key = generate_key()

    assert len(base64.urlsafe_b64decode(key)) == 32
    assert StaticKeyProvider(base64.urlsafe_b64decode(key))


def test_settings_provider_reads_env_key(monkeypatch: pytest.MonkeyPatch) -> None:
    key = generate_key()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    reset_settings_cache()

    assert SettingsKeyProvider().get_key() == base64.urlsafe_b64decode(key)


def test_settings_provider_fails_without_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    reset_settings_cache()

    with pytest.raises(EncryptionKeyMissingError, match="ENCRYPTION_KEY is not set"):
        SettingsKeyProvider().get_key()


def test_settings_provider_rejects_invalid_base64(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ENCRYPTION_KEY", "not base64!!")
    reset_settings_cache()

    with pytest.raises(EncryptionError, match="valid base64"):
        SettingsKeyProvider().get_key()


def test_key_is_secret_in_settings_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    key = generate_key()
    monkeypatch.setenv("ENCRYPTION_KEY", key)
    reset_settings_cache()

    assert key not in repr(get_settings())
    assert get_settings().encryption_key is not None
    assert isinstance(get_settings().encryption_key, SecretStr)


def test_ciphertext_is_redacted_from_log_text(monkeypatch: pytest.MonkeyPatch) -> None:
    ciphertext = service().encrypt("+919876543210")

    assert redact_text(f"stored {ciphertext}") == "stored [REDACTED_CIPHERTEXT]"


def test_ciphertext_never_reaches_the_rendered_log_stream() -> None:
    ciphertext = service().encrypt("M1234567")
    stream = StringIO()
    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonFormatter())
    handler.addFilter(RedactionFilter())
    logger = logging.getLogger("travelos.test.encryption")
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    try:
        logger.info("contact stored value=%s", ciphertext, extra={"ciphertext": ciphertext})
    finally:
        logger.removeHandler(handler)

    rendered = stream.getvalue()
    assert ciphertext not in rendered
    assert ENCRYPTED_PREFIX not in rendered
    payload = json.loads(rendered)
    assert payload["ciphertext"] == "[REDACTED_CIPHERTEXT]"


def test_global_provider_override_is_used_by_default() -> None:
    set_key_provider(StaticKeyProvider(TEST_KEY))
    try:
        assert default_key_provider().get_key() == TEST_KEY
    finally:
        set_key_provider(None)


def test_missing_default_key_raises_when_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ENCRYPTION_KEY", raising=False)
    reset_settings_cache()
    set_key_provider(None)
    try:
        with pytest.raises(EncryptionKeyMissingError):
            default_key_provider().get_key()
    finally:
        set_key_provider(StaticKeyProvider(TEST_KEY))


def test_date_helpers_roundtrip_via_string_form() -> None:
    svc = service()
    dob = date(2018, 1, 31)

    assert date.fromisoformat(svc.decrypt(svc.encrypt(dob.isoformat()))) == dob
