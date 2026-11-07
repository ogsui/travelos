"""Route a price that needs an owner decision (task 015d).

The approval matrix has one LOCKED A0 row for "quote below profit floor / floor
above ceiling". A0 means a human performs the action and the system may only
prepare data, so this module does not create an ``approval_requests`` row: the
approval service correctly denies that. Instead it creates an owner
``human_tasks`` row pointing at the stored calculation and audits the request.

Only ``below_floor`` and ``uncompetitive`` route. ``ok`` needs no decision, and
``needs_config`` has no price to decide: its gaps belong to configuration work,
not to a quote approval.

Routing is idempotent per calculation while the task is open or in progress, so
a retried quote cannot flood the owner queue.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.approval.matrix import Decision, resolve
from travelos.core.audit import Actor, AuditWriter
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.models import HumanTask
from travelos.core.money import Money
from travelos.pricing.calculator import PriceResult, PriceStatus
from travelos.pricing.errors import PricingValidationError
from travelos.pricing.records import PriceCalculationRecord

_OWNER_DECISION_ACTION: Final = "quote.below_floor"
_TASK_TYPE: Final = "pricing.owner_decision"
_SUBJECT_TYPE: Final = "price_calculation"
_OPEN_STATUSES: Final = ("open", "in_progress")
_ROUTED_STATUSES: Final = frozenset({PriceStatus.BELOW_FLOOR, PriceStatus.UNCOMPETITIVE})


@dataclass(frozen=True, slots=True)
class OwnerDecision:
    """The owner task that must decide a below-floor or uncompetitive price."""

    task_id: UUID
    action_type: str
    status: PriceStatus
    created: bool


def route_to_owner(
    db: Session,
    *,
    result: PriceResult,
    calculation_id: UUID,
    tenant_id: UUID = DEFAULT_TENANT_ID,
    actor: Actor | None = None,
    audit: AuditWriter | None = None,
) -> OwnerDecision | None:
    """Create (or reuse) the owner task for a price that cannot be auto-quoted.

    Returns ``None`` for ``ok`` and ``needs_config``. The cited calculation must
    already exist for the tenant and agree with the result's status and policy
    versions; a price that was never persisted must not reach the owner queue.
    """

    if result.status not in _ROUTED_STATUSES:
        return None

    decision = resolve(_OWNER_DECISION_ACTION)
    if decision.decision is not Decision.DENY or not decision.owner_only:
        raise PricingValidationError(
            f"{_OWNER_DECISION_ACTION} must remain a LOCKED A0 owner-only action"
        )

    row = db.get(PriceCalculationRecord, calculation_id)
    if row is None or row.tenant_id != tenant_id:
        raise PricingValidationError(f"price calculation {calculation_id} was not found")
    if row.status != result.status.value:
        raise PricingValidationError(
            f"price calculation {calculation_id} is {row.status}, not {result.status.value}"
        )
    if (row.rule_version, row.tax_table_version) != (
        result.rule_version,
        result.tax_table_version,
    ):
        raise PricingValidationError(
            f"price calculation {calculation_id} cites different rule or tax versions"
        )

    existing = (
        db.execute(
            select(HumanTask).where(
                HumanTask.tenant_id == tenant_id,
                HumanTask.task_type == _TASK_TYPE,
                HumanTask.subject_type == _SUBJECT_TYPE,
                HumanTask.subject_id == calculation_id,
                HumanTask.status.in_(_OPEN_STATUSES),
            )
        )
        .scalars()
        .first()
    )
    if existing is not None:
        return OwnerDecision(
            task_id=existing.id,
            action_type=_OWNER_DECISION_ACTION,
            status=result.status,
            created=False,
        )

    task = HumanTask(
        tenant_id=tenant_id,
        task_type=_TASK_TYPE,
        subject_type=_SUBJECT_TYPE,
        subject_id=calculation_id,
        title=_title(result.status, calculation_id),
        assignee_role="owner",
        priority="high",
        due_at=result.expires_at,
    )
    db.add(task)
    db.flush()

    writer = audit or AuditWriter()
    writer.record(
        db,
        action="pricing.owner_decision.requested",
        actor=actor or Actor.system(),
        subject_type=_SUBJECT_TYPE,
        subject_id=calculation_id,
        after={
            "action_type": _OWNER_DECISION_ACTION,
            "status": result.status.value,
            "floor_paise": _paise(result.floor),
            "ceiling_paise": _paise(result.ceiling),
            "customer_price_paise": _paise(result.customer_price),
            "rule_version": result.rule_version,
            "tax_table_version": result.tax_table_version,
        },
    )
    return OwnerDecision(
        task_id=task.id,
        action_type=_OWNER_DECISION_ACTION,
        status=result.status,
        created=True,
    )


def _title(status: PriceStatus, calculation_id: UUID) -> str:
    label = "Below-floor price" if status is PriceStatus.BELOW_FLOOR else "Uncompetitive price"
    return f"{label} needs an owner decision ({calculation_id})"


def _paise(amount: Money | None) -> int | None:
    return None if amount is None else amount.paise


__all__ = ["OwnerDecision", "route_to_owner"]
