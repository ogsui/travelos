"""Deterministic query identity for competitor price snapshots (task 014a2b1).

A snapshot is stored against a canonical hash of the comparability basis, so the
same question always maps to the same rows and two different products never share
a cache entry. The hash is part of the stored data, so changing how a basis is
canonicalised invalidates old rows rather than silently matching them.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any

import pytest

from travelos.market.models import ComparabilityBasis
from travelos.market.query import query_hash

START = date(2027, 2, 10)
END = date(2027, 2, 13)


def basis(**overrides: Any) -> ComparabilityBasis:
    values: dict[str, Any] = {
        "product_type": "flight",
        "route": "Delhi-Goa",
        "start_date": START,
        "end_date": END,
        "adults": 2,
        "children": 0,
        "infants": 0,
        "fare_class": "economy",
        "checked_bags": 1,
        "refundable": True,
        "currency": "INR",
    }
    values.update(overrides)
    return ComparabilityBasis.model_validate(values)


def test_the_hash_is_lowercase_sha256_hex() -> None:
    digest = query_hash(basis())

    assert re.fullmatch(r"[0-9a-f]{64}", digest)


def test_the_same_basis_always_hashes_the_same() -> None:
    assert query_hash(basis()) == query_hash(basis())


def test_the_canonical_form_is_pinned() -> None:
    """A canonicalisation change must be a visible decision, not a silent drift."""

    assert query_hash(basis()) == (
        "f046a31685d427d633d50f98115e5d039741714e67448a8f3e3faeae9c78c0d2"
    )


@pytest.mark.parametrize(
    "overrides",
    [
        {"product_type": "hotel"},
        {"route": "Goa-Delhi"},
        {"start_date": date(2027, 2, 11)},
        {"end_date": date(2027, 2, 14)},
        {"adults": 3},
        {"children": 1},
        {"infants": 1},
        {"fare_class": "business"},
        {"fare_class": None},
        {"checked_bags": 2},
        {"checked_bags": None},
        {"refundable": False},
        {"refundable": None},
        {"currency": "USD"},
    ],
)
def test_every_identity_dimension_changes_the_hash(overrides: dict[str, Any]) -> None:
    assert query_hash(basis(**overrides)) != query_hash(basis())


def test_a_one_way_trip_is_its_own_query() -> None:
    assert query_hash(basis(end_date=None)) != query_hash(basis())
