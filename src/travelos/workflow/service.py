"""Transition service: pure machine result + audit + outbox in one transaction.

The service does not persist the state itself. Domain tables (``inquiries``,
``quotes``, ``bookings`` ...) own their ``state`` column, and each caller writes
that column in the same transaction. Keeping the engine stateless means one
tested implementation serves every workflow without a second source of truth.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy.orm import Session

from travelos.core.audit import Actor, AuditWriter
from travelos.workflow.definitions import get_machine
from travelos.workflow.outbox import OutboxWriter
from travelos.workflow.state_machine import TransitionResult

_AUDIT_ACTION = "workflow.transition"


class WorkflowService:
    """Applies a trigger and records the result atomically."""

    def __init__(
        self,
        *,
        audit: AuditWriter | None = None,
        outbox: OutboxWriter | None = None,
    ) -> None:
        self._audit = audit or AuditWriter()
        self._outbox = outbox or OutboxWriter()

    def apply(
        self,
        db: Session,
        *,
        machine: str,
        subject_type: str,
        subject_id: UUID,
        state: str,
        trigger: str,
        context: Mapping[str, object] | None = None,
        payload: Mapping[str, Any] | None = None,
        actor: Actor | None = None,
        tenant_id: UUID | None = None,
    ) -> TransitionResult:
        """Fire one trigger; raises a typed workflow error if it is not allowed.

        On success an audit row and one outbox event per effect are added to
        ``db``. The caller commits; nothing is written when the trigger is
        rejected.
        """

        result = get_machine(machine).fire(state, trigger, context)
        resolved_actor = actor or Actor.system()
        self._audit.record_change(
            db,
            action=_AUDIT_ACTION,
            actor=resolved_actor,
            subject_type=subject_type,
            subject_id=subject_id,
            before={"state": result.source},
            after={
                "state": result.target,
                "trigger": result.trigger,
                "machine": result.machine,
            },
        )

        base_payload: dict[str, Any] = {
            "machine": result.machine,
            "source": result.source,
            "target": result.target,
            "trigger": result.trigger,
            "subject_type": subject_type,
            "subject_id": subject_id,
        }
        for effect in result.effects:
            effect_payload = dict(payload or {})
            effect_payload.update(base_payload)
            self._outbox.enqueue(
                db,
                event_type=effect,
                tenant_id=tenant_id,
                payload=effect_payload,
            )
        return result
