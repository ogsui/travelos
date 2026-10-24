"""TOTP tests, including the RFC 6238 reference vectors."""

from __future__ import annotations

import base64
from urllib.parse import parse_qs, urlparse

import pytest

from travelos.security.totp import (
    generate_code,
    generate_secret,
    provisioning_uri,
    verify_code,
)

# RFC 6238 Appendix B: secret "12345678901234567890" (ASCII), SHA-1.
RFC_SECRET = base64.b32encode(b"12345678901234567890").decode("ascii").rstrip("=")

RFC_VECTORS = [
    (59, "94287082"),
    (1_111_111_109, "07081804"),
    (1_111_111_111, "14050471"),
    (1_234_567_890, "89005924"),
    (2_000_000_000, "69279037"),
    (20_000_000_000, "65353130"),
]


@pytest.mark.parametrize(("at", "expected"), RFC_VECTORS)
def test_rfc6238_vectors_with_eight_digits(at: int, expected: str) -> None:
    assert generate_code(RFC_SECRET, at=at, digits=8) == expected


def test_default_six_digit_code_is_the_tail_of_the_rfc_code() -> None:
    assert generate_code(RFC_SECRET, at=59) == "287082"


@pytest.mark.parametrize(("at", "expected"), RFC_VECTORS)
def test_verification_accepts_the_valid_code(at: int, expected: str) -> None:
    assert verify_code(RFC_SECRET, expected, at=at, digits=8) is True


def test_verification_allows_one_period_of_drift_each_way() -> None:
    code = generate_code(RFC_SECRET, at=59)

    assert verify_code(RFC_SECRET, code, at=59) is True
    assert verify_code(RFC_SECRET, code, at=29) is True
    assert verify_code(RFC_SECRET, code, at=89) is True
    assert verify_code(RFC_SECRET, code, at=150) is False
    assert verify_code(RFC_SECRET, code, at=29, window=0) is False


def test_window_is_bounded() -> None:
    with pytest.raises(ValueError, match="window"):
        verify_code(RFC_SECRET, "123456", window=6)


@pytest.mark.parametrize("code", ["", "12345", "1234567", "abcdef", "12 456"])
def test_malformed_codes_are_rejected(code: str) -> None:
    assert verify_code(RFC_SECRET, code) is False


def test_generated_secret_is_base32_and_random() -> None:
    first = generate_secret()
    second = generate_secret()

    assert first != second
    assert first == first.upper().rstrip("=")
    # 20 random bytes -> 32 base32 characters without padding.
    assert len(first) == 32
    assert base64.b32decode(first + "=" * (-len(first) % 8))


def test_provisioning_uri_carries_secret_and_issuer() -> None:
    uri = provisioning_uri("JBSWY3DPEHPK3PXP", "owner@example.test", issuer="TravelOS")

    parsed = urlparse(uri)
    assert parsed.scheme == "otpauth"
    assert parsed.netloc == "totp"
    assert parsed.path.endswith("TravelOS%3Aowner%40example.test")
    query = parse_qs(parsed.query)
    assert query["secret"] == ["JBSWY3DPEHPK3PXP"]
    assert query["issuer"] == ["TravelOS"]
    assert query["algorithm"] == ["SHA1"]
    assert query["digits"] == ["6"]
    assert query["period"] == ["30"]
