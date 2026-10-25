"""Contact fingerprints normalize values and are keyed by channel and secret."""

from __future__ import annotations

from travelos.crm.fingerprints import ContactFingerprinter
from travelos.security.encryption import StaticKeyProvider


def fingerprinter(secret: bytes) -> ContactFingerprinter:
    return ContactFingerprinter(StaticKeyProvider(secret))


def test_equivalent_email_values_have_same_fingerprint() -> None:
    fingerprints = fingerprinter(b"a" * 32)

    assert fingerprints.fingerprint(
        "email", "  Alice.Example@Example.Test "
    ) == fingerprints.fingerprint("email", "alice.example@example.test")


def test_equivalent_phone_values_have_same_fingerprint() -> None:
    fingerprints = fingerprinter(b"a" * 32)

    assert fingerprints.fingerprint("phone", "+91 (90000) 00001") == fingerprints.fingerprint(
        "phone", "+919000000001"
    )


def test_fingerprint_is_scoped_to_channel_and_secret() -> None:
    phone = "+919000000001"
    expected = fingerprinter(b"a" * 32).fingerprint("email", phone)

    assert fingerprinter(b"a" * 32).fingerprint("whatsapp", phone) != expected
    assert fingerprinter(b"b" * 32).fingerprint("email", phone) != expected


def test_fingerprint_does_not_contain_contact_value() -> None:
    value = "alice@example.test"
    digest = fingerprinter(b"a" * 32).fingerprint("email", value)

    assert value not in digest
    assert len(digest) == 64
