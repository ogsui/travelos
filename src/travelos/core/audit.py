"""Append-only audit writer.

Every state change, approval, outbound action, price calculation, LLM call and
config change goes through this writer. Records are written in the caller's
transaction so a change and its audit entry commit (or roll back) together.

Values are made JSON-safe and redacted (passwords, emails, phones, passport
numbers, ciphertext) before they reach the database.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from enum import Enum
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.orm import Session

from travelos.core.models import AuditLog
from travelos.core.redaction import redact_mapping

ActorType = Literal["user", "system", "ai", "service"]
_ACTOR_TYPES = frozenset({"user", "system", "ai", "service"})


@dataclasses.dataclass(frozen=True, slots=True)
class Actor:
    """Who caused the change. Never a free-text name."""

    type: ActorType = "system"
    id: UUID | None = None

    def __post_init__(self) -> None:
        if self.type not in _ACTOR_TYPES:
            raise ValueError(f"unknown actor type: {self.type!r}")

    @classmethod
    def user(cls, user_id: UUID) -> Actor:
        return cls(type="user", id=user_id)

    @classmethod
    def system(cls) -> Actor:
        return cls(type="system")

    @classmethod
    def ai(cls, run_id: UUID | None = None) -> Actor:
        return cls(type="ai", id=run_id)


def jsonable(value: Any) -> Any:
    """Convert a value into something ``jsonb`` accepts, recursively."""

    if value is None or isinstance(value, bool | int | float | str):
        return value
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Enum):
        return value.value
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return jsonable(dataclasses.asdict(value))
    if isinstance(value, Mapping):
        return {str(key): jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence | set | frozenset):
        return [jsonable(item) for item in value]
    return str(value)


def build_diff(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Return the redacted, JSON-safe subset of fields that actually changed.

    Storing only the changed fields keeps audit rows small and avoids copying
    unrelated personal data into the log. Removed and added keys are included.
    """

    before_changes: dict[str, Any] = {}
    after_changes: dict[str, Any] = {}
    for key in before.keys() | after.keys():
        old = before.get(key, None)
        new = after.get(key, None)
        if key in before and key in after and old == new:
            continue
        if key in before:
            before_changes[key] = old
        if key in after:
            after_changes[key] = new

    safe_before = redact_mapping(jsonable(before_changes))
    safe_after = redact_mapping(jsonable(after_changes))
    return safe_before, safe_after


class AuditWriter:
    """Writes audit rows; callers own the transaction."""

    def record(
        self,
        db: Session,
        *,
        action: str,
        actor: Actor,
        subject_type: str | None = None,
        subject_id: UUID | None = None,
        before: Mapping[str, Any] | None = None,
        after: Mapping[str, Any] | None = None,
        ip: str | None = None,
    ) -> AuditLog:
        entry = AuditLog(
            actor_type=actor.type,
            actor_id=actor.id,
            action=action,
            subject_type=subject_type,
            subject_id=subject_id,
            before=redact_mapping(jsonable(dict(before))) if before is not None else None,
            after=redact_mapping(jsonable(dict(after))) if after is not None else None,
            ip=ip,
        )
        db.add(entry)
        return entry

    def record_change(
        self,
        db: Session,
        *,
        action: str,
        actor: Actor,
        subject_type: str,
        subject_id: UUID,
        before: Mapping[str, Any],
        after: Mapping[str, Any],
        ip: str | None = None,
    ) -> AuditLog:
        changed_before, changed_after = build_diff(before, after)
        return self.record(
            db,
            action=action,
            actor=actor,
            subject_type=subject_type,
            subject_id=subject_id,
            before=changed_before,
            after=changed_after,
            ip=ip,
        )
