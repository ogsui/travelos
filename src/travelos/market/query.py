"""Canonical identity for one competitor-price question (task 014a2b1).

A snapshot is stored against the canonical hash of its comparability basis, so
the same product always maps to the same rows and two different products never
share a cache entry. The hash is part of the stored row: changing the canonical
form makes old rows stop matching instead of silently joining a new query.
"""

from __future__ import annotations

import hashlib
import json

from travelos.market.models import ComparabilityBasis


def query_hash(basis: ComparabilityBasis) -> str:
    """Return the lowercase sha256 of the basis' canonical JSON form."""

    payload = json.dumps(
        basis.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
