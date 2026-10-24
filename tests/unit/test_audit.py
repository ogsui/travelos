"""Audit helpers: JSON safety, before/after diffs and redaction."""

from __future__ import annotations

import dataclasses
from datetime import UTC, date, datetime
from enum import StrEnum
from uuid import UUID, uuid4

import pytest

from travelos.core.audit import Actor, build_diff, jsonable
from travelos.core.redaction import REDACTED


class Colour(StrEnum):
    RED = "red"


@dataclasses.dataclass(frozen=True)
class Sample:
    name: str
    count: int


def test_jsonable_converts_known_types_recursively() -> None:
    identifier = uuid4()
    payload = {
        "id": identifier,
        "at": datetime(2026, 10, 24, 12, 30, tzinfo=UTC),
        "day": date(2026, 10, 24),
        "colour": Colour.RED,
        "sample": Sample(name="synthetic", count=2),
        "nested": {"ids": [identifier]},
        "single": {"x"},
        "flag": True,
        "amount": 123_45,
    }

    converted = jsonable(payload)

    assert converted["id"] == str(identifier)
    assert converted["at"] == "2026-10-24T12:30:00+00:00"
    assert converted["day"] == "2026-10-24"
    assert converted["colour"] == "red"
    assert converted["sample"] == {"name": "synthetic", "count": 2}
    assert converted["nested"] == {"ids": [str(identifier)]}
    assert converted["single"] == ["x"]
    assert converted["flag"] is True
    assert converted["amount"] == 12345
    # The result must be acceptable to json.dumps without a default hook.
    import json

    json.dumps(converted)


def test_build_diff_keeps_only_changed_fields() -> None:
    before = {"email": "old@example.test", "role": "agent", "unchanged": 1}
    after = {"email": "old@example.test", "role": "accountant", "unchanged": 1}

    changed_before, changed_after = build_diff(before, after)

    assert changed_before == {"role": "agent"}
    assert changed_after == {"role": "accountant"}


def test_build_diff_includes_added_and_removed_keys() -> None:
    changed_before, changed_after = build_diff({"removed": 1}, {"added": 2})

    assert changed_before == {"removed": 1}
    assert changed_after == {"added": 2}


def test_build_diff_redacts_identifiers_and_credentials() -> None:
    before = {
        "email": "owner@example.test",
        "password": "hunter2",
        "note": "call me on +919000000123",
        "phone": "+919000000123",
        "passport_no": "M1234567",
    }
    after = {
        "email": "new@example.test",
        "password": "hunter3",
        "note": "call me on +919000000456",
        "phone": "+919000000456",
        "passport_no": "M7654321",
    }

    changed_before, changed_after = build_diff(before, after)

    for value in (*changed_before.values(), *changed_after.values()):
        assert "owner@example.test" not in str(value)
        assert "hunter" not in str(value)
        assert "M1234567" not in str(value)
        assert "9000000123" not in str(value)

    assert changed_before["email"] == REDACTED
    assert changed_before["password"] == REDACTED
    assert "note" in changed_before


def test_actor_requires_a_known_type() -> None:
    with pytest.raises(ValueError, match="unknown actor type"):
        Actor(type="robot")  # type: ignore[arg-type]


def test_actor_factories_carry_the_identifier() -> None:
    user_id = uuid4()
    run_id = uuid4()

    assert Actor.user(user_id) == Actor(type="user", id=user_id)
    assert Actor.system() == Actor(type="system", id=None)
    assert Actor.ai(run_id) == Actor(type="ai", id=run_id)
    assert Actor.user(user_id).id == UUID(str(user_id))
