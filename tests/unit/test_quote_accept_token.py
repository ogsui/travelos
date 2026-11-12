"""Accept-token hashing rules (task 016c1).

The database stores only a sha256 digest. These tests pin the digest shape and
the refusal of a blank, short or oversized presented token before it reaches the
database.
"""

from __future__ import annotations

import hashlib

import pytest

from travelos.quotes.acceptance import new_accept_token, token_digest
from travelos.quotes.errors import QuoteLinkNotFoundError


def test_tokens_are_random_and_url_safe() -> None:
    first = new_accept_token()
    second = new_accept_token()

    assert first != second
    assert len(first) >= 32
    assert first == first.strip()
    assert first.isascii()


def test_the_digest_is_lowercase_sha256_hex() -> None:
    token = "synthetic-token-" + "a" * 30

    digest = token_digest(token)

    assert digest == hashlib.sha256(token.encode("utf-8")).hexdigest()
    assert digest == digest.lower()
    assert len(digest) == 64


@pytest.mark.parametrize("token", ["", "   ", "short", "x" * 513])
def test_a_malformed_token_is_not_found(token: str) -> None:
    with pytest.raises(QuoteLinkNotFoundError):
        token_digest(token)
