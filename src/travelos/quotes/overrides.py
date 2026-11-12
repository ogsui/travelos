"""Record the owner's decision on a non-``ok`` price (task 016c2c).

The approval matrix keeps ``quote.below_floor`` as a LOCKED A0 action: a human
decides, and task 015d only prepared a ``pricing.owner_decision`` task. This module
is that human decision, recorded as an append-only row rather than by editing the
task or the calculation.

The owner may choose a customer total. The total is not typed straight into the
quote: it is turned into a new deterministic calculation by
``assemble_owner_price`` (same supplier costs, margins and tax lines, one explicit
difference line), and the override cites that calculation. A revision can then only
cite a non-``ok`` calculation together with the override that authorised it.

Nothing here calculates tax, moves money, sends anything or bypasses the approval
layer. The caller owns the transaction.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.core.audit import Actor, AuditWriter
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.models import HumanTask, User
from travelos.core.money import Money
from travelos.pricing.arithmetic import FxSnapshot
from travelos.pricing.calculator import (
    ItemCost,
    PriceRequest,
    PriceStatus,
    assemble_owner_price,
)
from travelos.pricing.errors import PricingValidationError
from travelos.pricing.models import CustomerSegment
from travelos.pricing.records import PriceCalculationRecord
from travelos.pricing.store import PricingStore, StoredRuleSet, StoredTaxTable
from travelos.quotes.errors import (
    QuotePriceCalculationNotFoundError,
    QuotePriceOverrideError,
    QuotePriceOverrideNotFoundError,
)
from travelos.quotes.records import QuotePriceOverrideRecord

_DECISION_TASK: str = "pricing.owner_decision"
_CALCULATION_SUBJECT: str = "price_calculation"
_OPEN_TASK_STATUSES: frozenset[str] = frozenset({"open", "in_progress"})
_OVERRIDABLE: frozenset[str] = frozenset(
    {PriceStatus.BELOW_FLOOR.value, PriceStatus.UNCOMPETITIVE.value}
)
_REASON_MIN = 5
_REASON_MAX = 2_000


class _Clock(Protocol):
    """Anything that reports the current instant, so tests can pin the decision time."""

    def now(self) -> datetime: ...


class QuotePriceOverrideService:
    """Record and read owner price overrides for one tenant."""

    def __init__(
        self,
        db: Session,
        *,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        audit: AuditWriter | None = None,
        clock: _Clock | None = None,
    ) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._audit = audit or AuditWriter()
        self._clock = clock

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def record(
        self,
        calculation_id: UUID,
        *,
        owner_total: Money,
        reason: str,
        actor: Actor,
        task_id: UUID | None = None,
        now: datetime | None = None,
    ) -> QuotePriceOverrideRecord:
        """Authorise a below-floor or uncompetitive calculation at the owner's total."""

        reviewer = self._require_owner(actor)
        decided_at = self._moment(now)
        text = self._reason(reason)

        pricing = PricingStore(self._db, tenant_id=self._tenant_id)
        source = pricing.get_calculation(calculation_id)
        if source is None:
            raise QuotePriceCalculationNotFoundError(
                f"price calculation {calculation_id} was not found"
            )
        if source.status not in _OVERRIDABLE:
            raise QuotePriceOverrideError(
                f"price calculation {calculation_id} is {source.status}; only a "
                "below_floor or uncompetitive price needs an owner override"
            )
        if source.currency != "INR":
            raise QuotePriceOverrideError(
                f"price calculation {calculation_id} is in {source.currency}; quotes are INR-only"
            )

        task = self._decision_task(calculation_id, task_id)
        rules = pricing.get_rule_set(source.rule_set_id)
        table = pricing.get_tax_table(source.tax_table_id)
        if rules is None or table is None:
            raise QuotePriceOverrideError(
                f"price calculation {calculation_id} cites a rule set or tax table "
                "that is not available"
            )

        request = self._request(source, rules, table)
        try:
            result = assemble_owner_price(request, owner_total)
        except PricingValidationError as exc:
            raise QuotePriceOverrideError(str(exc)) from exc
        if result.status is PriceStatus.NEEDS_CONFIG:
            raise QuotePriceOverrideError(
                "the owner total cannot be assembled deterministically: " + " | ".join(result.gaps)
            )
        assert result.customer_price is not None

        authorised = pricing.record_calculation(
            request,
            result,
            rule_set_id=source.rule_set_id,
            tax_table_id=source.tax_table_id,
            actor=actor,
        )

        row = QuotePriceOverrideRecord(
            tenant_id=self._tenant_id,
            source_calculation_id=source.id,
            authorised_calculation_id=authorised.id,
            decision_task_id=task.id,
            owner_user_id=reviewer.id,
            reason=text,
            status=source.status,
            decided_at=decided_at,
            floor_paise=_paise(source.floor_paise),
            target_paise=_paise(source.target_paise),
            ceiling_paise=_paise(source.ceiling_paise),
            source_customer_price_paise=source.customer_price_paise,
            owner_total_paise=result.customer_price.paise,
            currency=result.customer_price.currency,
        )
        self._db.add(row)
        task.status = "done"
        self._db.flush()

        self._audit.record(
            self._db,
            action="quote.price_override.recorded",
            actor=actor,
            subject_type="quote_price_override",
            subject_id=row.id,
            after={
                "source_calculation_id": str(source.id),
                "authorised_calculation_id": str(authorised.id),
                "decision_task_id": str(task.id),
                "status": row.status,
                "source_customer_price_paise": row.source_customer_price_paise,
                "owner_total_paise": row.owner_total_paise,
                "currency": row.currency,
                "reason_length": len(text),
                "decided_at": decided_at.isoformat(),
            },
        )
        self._audit.record(
            self._db,
            action="pricing.owner_decision.completed",
            actor=actor,
            subject_type=_CALCULATION_SUBJECT,
            subject_id=source.id,
            after={
                "task_id": str(task.id),
                "task_status": task.status,
                "override_id": str(row.id),
                "authorised_calculation_id": str(authorised.id),
                "owner_total_paise": row.owner_total_paise,
            },
        )
        self._db.flush()
        return row

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def get(self, override_id: UUID) -> QuotePriceOverrideRecord:
        """Read one override, or fail closed if this tenant does not own it."""

        row = self._db.get(QuotePriceOverrideRecord, override_id)
        if row is None or row.tenant_id != self._tenant_id:
            raise QuotePriceOverrideNotFoundError(
                f"quote price override {override_id} was not found"
            )
        return row

    def for_calculation(self, calculation_id: UUID) -> QuotePriceOverrideRecord | None:
        """The newest override that authorised ``calculation_id``, if any."""

        return (
            self._db.execute(
                select(QuotePriceOverrideRecord)
                .where(
                    QuotePriceOverrideRecord.tenant_id == self._tenant_id,
                    QuotePriceOverrideRecord.authorised_calculation_id == calculation_id,
                )
                .order_by(QuotePriceOverrideRecord.created_at.desc())
            )
            .scalars()
            .first()
        )

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _require_owner(self, actor: Actor) -> User:
        if actor.type != "user" or actor.id is None:
            raise QuotePriceOverrideError("only a signed-in owner may override a quote price")
        user = self._db.get(User, actor.id)
        if user is None or user.tenant_id != self._tenant_id:
            raise QuotePriceOverrideError(f"user {actor.id} was not found")
        if not user.is_active:
            raise QuotePriceOverrideError(f"user {actor.id} is not active")
        if user.role != "owner":
            raise QuotePriceOverrideError(
                f"user {actor.id} is {user.role}; only the owner may override a price"
            )
        return user

    def _decision_task(self, calculation_id: UUID, task_id: UUID | None) -> HumanTask:
        query = select(HumanTask).where(
            HumanTask.tenant_id == self._tenant_id,
            HumanTask.task_type == _DECISION_TASK,
            HumanTask.subject_type == _CALCULATION_SUBJECT,
            HumanTask.subject_id == calculation_id,
        )
        if task_id is not None:
            query = query.where(HumanTask.id == task_id)
        task = self._db.execute(query).scalars().first()
        if task is None:
            raise QuotePriceOverrideError(
                f"price calculation {calculation_id} has no owner decision task"
            )
        if task.status not in _OPEN_TASK_STATUSES:
            raise QuotePriceOverrideError(
                f"owner decision task {task.id} is {task.status}; a decision can "
                "only be recorded once"
            )
        return task

    def _request(
        self,
        source: PriceCalculationRecord,
        rules: StoredRuleSet,
        table: StoredTaxTable,
    ) -> PriceRequest:
        """Rebuild the exact request the source calculation was made from.

        The stored inputs keep the original ``now``, so the replacement calculation
        inherits the same expiry. An override never extends how long a price or a
        supplier snapshot stays valid.
        """

        inputs = source.inputs
        try:
            return PriceRequest(
                rules=rules.rules,
                tax_table=table.table,
                items=tuple(ItemCost.model_validate(item) for item in inputs["items"]),
                segment=CustomerSegment(inputs["segment"]),
                gateway_fee=Money.from_dict(inputs["gateway_fee"]),
                competitor_reference=_money(inputs.get("competitor_reference")),
                proposed_price=_money(inputs.get("proposed_price")),
                fx_snapshots=tuple(
                    FxSnapshot.model_validate(snapshot)
                    for snapshot in inputs.get("fx_snapshots", ())
                ),
                now=datetime.fromisoformat(inputs["now"]),
            )
        except (KeyError, TypeError, ValueError, PricingValidationError) as exc:
            raise QuotePriceOverrideError(
                f"price calculation {source.id} has inputs that cannot be replayed: {exc}"
            ) from exc

    def _reason(self, reason: str) -> str:
        text = reason.strip()
        if len(text) < _REASON_MIN:
            raise QuotePriceOverrideError(
                f"the override reason must be at least {_REASON_MIN} characters"
            )
        if len(text) > _REASON_MAX:
            raise QuotePriceOverrideError(
                f"the override reason must be at most {_REASON_MAX} characters"
            )
        return text

    def _moment(self, now: datetime | None) -> datetime:
        if now is None:
            clock = self._clock
            now = clock.now() if clock is not None else datetime.now(tz=UTC)
        if now.tzinfo is None or now.utcoffset() is None:
            raise QuotePriceOverrideError("the decision time must be timezone-aware")
        return now.astimezone(UTC)


def _money(raw: object) -> Money | None:
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise ValueError("a stored amount must be an object")
    return Money.from_dict(raw)


def _paise(value: int | None) -> int:
    if value is None:
        raise QuotePriceOverrideError("the source calculation has no stored amount to copy")
    return value


__all__ = ["QuotePriceOverrideService"]
