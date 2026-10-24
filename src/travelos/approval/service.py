"""Approval lifecycle, autonomy settings, seeding and auto-demotion.

The service is deterministic: no LLM decides anything here. It resolves policy,
records proposals/decisions, drives expiry and escalation timers, and updates
the aggregate metrics used to demote autonomy.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from travelos.approval.errors import (
    ApprovalNotFoundError,
    ApprovalPermissionError,
    ApprovalStateError,
    DistinctReviewerError,
    EditRequiresChangeError,
    PolicyDeniedError,
    ReasonRequiredError,
    SelfApprovalError,
    UnhandledTimerError,
)
from travelos.approval.matrix import (
    CATALOG,
    ActionSpec,
    AutonomyLevel,
    Decision,
    PolicyDecision,
    get_spec,
    resolve,
    validate_level,
)
from travelos.approval.models import ApprovalRequest
from travelos.core.audit import Actor, AuditWriter, jsonable
from travelos.core.clock import Clock, SystemClock
from travelos.core.config import Settings
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.ids import uuid7
from travelos.core.models import AutonomyMetric, AutonomyPolicy, HumanTask, IdempotencyKey
from travelos.workflow.outbox import OutboxWriter
from travelos.workflow.timers import TimerKind, TimerService

logger = logging.getLogger(__name__)

DEMOTION_WINDOW = timedelta(days=30)
DEMOTION_MIN_PROPOSALS = 50
DEMOTION_MIN_APPROVAL_RATE = 0.85
_DEMOTION_SCOPE = "autonomy.demote"
_APPROVAL_SUBJECT = "approval_request"
_MAX_TITLE = 200
_METRIC_FIELDS = frozenset({"proposals", "approved_unchanged", "edited", "rejected", "incidents"})


class ReviewDecision(StrEnum):
    """What a reviewer can do with a pending request."""

    APPROVE = "approve"
    EDIT = "edit"
    REJECT = "reject"


@dataclass(frozen=True, slots=True)
class Submission:
    """Result of asking the engine whether an action may proceed."""

    decision: PolicyDecision
    approval: ApprovalRequest | None


@dataclass(frozen=True, slots=True)
class SeedReport:
    """What the idempotent policy seed changed."""

    inserted: int = 0
    repaired: int = 0


@dataclass(frozen=True, slots=True)
class Demotion:
    """One automatic demotion, for the worker result and tests."""

    action_type: str
    from_level: AutonomyLevel
    to_level: AutonomyLevel
    reason: str


def _day_window(now: datetime) -> tuple[datetime, datetime]:
    start = now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    return start, start + timedelta(days=1)


def _title(value: str) -> str:
    return value[:_MAX_TITLE]


def _as_dict(value: Mapping[str, Any] | None) -> dict[str, Any]:
    result: dict[str, Any] = jsonable(dict(value or {}))
    return result


def _snapshot(request: ApprovalRequest) -> dict[str, Any]:
    return {
        "status": request.status,
        "level": request.level,
        "proposed": request.proposed,
        "final": request.final,
        "reason": request.reason,
        "reviewer_id": request.reviewer_id,
        "second_reviewer_id": request.second_reviewer_id,
        "approvals_count": request.approvals_count,
    }


class AutonomyService:
    """Owner settings, catalogue resolution, seeding and demotion."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        audit: AuditWriter | None = None,
        outbox: OutboxWriter | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._audit = audit or AuditWriter()
        self._outbox = outbox or OutboxWriter(self._clock)

    # -- resolution -------------------------------------------------------

    def policy_row(
        self, db: Session, action_type: str, *, tenant_id: UUID | None = None
    ) -> AutonomyPolicy | None:
        tenant = tenant_id or DEFAULT_TENANT_ID
        return (
            db.execute(
                select(AutonomyPolicy).where(
                    AutonomyPolicy.tenant_id == tenant,
                    AutonomyPolicy.action_type == action_type,
                )
            )
            .scalars()
            .first()
        )

    def decision_for(
        self,
        db: Session,
        action_type: str,
        *,
        value_paise: int = 0,
        tenant_id: UUID | None = None,
    ) -> PolicyDecision:
        """Resolve an action against the catalogue and the owner's row."""

        get_spec(action_type)  # fail closed on unknown actions
        row = self.policy_row(db, action_type, tenant_id=tenant_id)
        configured: AutonomyLevel | None = None
        if row is not None:
            try:
                configured = AutonomyLevel(row.level)
            except ValueError:
                logger.warning(
                    "invalid stored autonomy level; using catalogue default",
                    extra={"action_type": action_type, "level": row.level},
                )
        return resolve(
            action_type,
            level=configured,
            value_paise=value_paise,
            policy_requires_four_eyes=bool(row and row.requires_four_eyes),
            threshold_paise=int(row.value_threshold_paise) if row is not None else 0,
        )

    def list_states(
        self, db: Session, *, tenant_id: UUID | None = None
    ) -> list[tuple[ActionSpec, AutonomyPolicy | None, PolicyDecision]]:
        rows = {
            row.action_type: row
            for row in db.execute(
                select(AutonomyPolicy).where(
                    AutonomyPolicy.tenant_id == (tenant_id or DEFAULT_TENANT_ID)
                )
            ).scalars()
        }
        states: list[tuple[ActionSpec, AutonomyPolicy | None, PolicyDecision]] = []
        for action_type in sorted(CATALOG):
            spec = CATALOG[action_type]
            states.append(
                (
                    spec,
                    rows.get(action_type),
                    self.decision_for(db, action_type, tenant_id=tenant_id),
                )
            )
        return states

    # -- owner updates ----------------------------------------------------

    def update_policy(
        self,
        db: Session,
        *,
        action_type: str,
        level: AutonomyLevel | str,
        value_threshold_paise: int = 0,
        requires_four_eyes: bool = False,
        notes: str | None = None,
        actor_id: UUID | None,
        actor_role: str = "owner",
        tenant_id: UUID | None = None,
    ) -> AutonomyPolicy:
        """Change one policy. Only the owner may call this at the API layer."""

        if actor_role != "owner":
            raise ApprovalPermissionError(action_type)
        if value_threshold_paise < 0:
            raise ValueError("value_threshold_paise must not be negative")
        spec = get_spec(action_type)
        parsed = AutonomyLevel(level)
        validate_level(spec, parsed)
        tenant = tenant_id or DEFAULT_TENANT_ID
        row = self.policy_row(db, action_type, tenant_id=tenant)
        actor = Actor.user(actor_id) if actor_id is not None else Actor.system()
        now = self._clock.now()

        before: dict[str, Any] = {}
        if row is None:
            row = AutonomyPolicy(
                tenant_id=tenant,
                action_type=action_type,
                level=parsed.value,
                value_threshold_paise=value_threshold_paise,
                currency="INR",
                requires_four_eyes=requires_four_eyes,
                notes=notes,
                updated_by=actor_id,
                effective_from=now,
            )
            db.add(row)
            db.flush()
            after = {
                "level": parsed.value,
                "value_threshold_paise": value_threshold_paise,
                "requires_four_eyes": requires_four_eyes,
                "notes": notes,
            }
        else:
            before = {
                "level": row.level,
                "value_threshold_paise": int(row.value_threshold_paise),
                "requires_four_eyes": row.requires_four_eyes,
                "notes": row.notes,
            }
            row.level = parsed.value
            row.value_threshold_paise = value_threshold_paise
            row.requires_four_eyes = requires_four_eyes
            row.notes = notes
            row.updated_by = actor_id
            row.effective_from = now
            after = {
                "level": parsed.value,
                "value_threshold_paise": value_threshold_paise,
                "requires_four_eyes": requires_four_eyes,
                "notes": notes,
            }
            db.flush()

        self._audit.record_change(
            db,
            action="autonomy.policy_changed",
            actor=actor,
            subject_type="autonomy_policy",
            subject_id=row.id,
            before=before,
            after=after,
        )
        self._outbox.enqueue(
            db,
            event_type="autonomy.policy_changed",
            tenant_id=tenant,
            payload={
                "action_type": action_type,
                "level": parsed.value,
                "before": before,
                "after": after,
            },
        )
        return row

    # -- seed -------------------------------------------------------------

    def seed(self, db: Session, *, tenant_id: UUID | None = None) -> SeedReport:
        """Insert missing policies and repair unsafe rows. Idempotent."""

        tenant = tenant_id or DEFAULT_TENANT_ID
        inserted = 0
        repaired = 0
        for action_type in sorted(CATALOG):
            spec = CATALOG[action_type]
            row = self.policy_row(db, action_type, tenant_id=tenant)
            if row is None:
                row = AutonomyPolicy(
                    tenant_id=tenant,
                    action_type=action_type,
                    level=spec.default_level.value,
                    value_threshold_paise=0,
                    currency="INR",
                    requires_four_eyes=False,
                    notes=spec.notes or None,
                )
                db.add(row)
                db.flush()
                inserted += 1
                self._audit.record(
                    db,
                    action="autonomy.policy_seeded",
                    actor=Actor.system(),
                    subject_type="autonomy_policy",
                    subject_id=row.id,
                    after={
                        "action_type": action_type,
                        "level": row.level,
                        "locked": spec.locked,
                        "max_level": spec.max_level.value,
                    },
                )
                continue

            try:
                current = AutonomyLevel(row.level)
            except ValueError:
                current = spec.default_level
            if spec.locked:
                safe = spec.default_level
            else:
                safe = current if current.rank <= spec.max_level.rank else spec.max_level
            if safe is not current:
                before = {"level": row.level}
                row.level = safe.value
                db.flush()
                repaired += 1
                self._audit.record_change(
                    db,
                    action="autonomy.policy_seed_repaired",
                    actor=Actor.system(),
                    subject_type="autonomy_policy",
                    subject_id=row.id,
                    before=before,
                    after={"level": safe.value, "max_level": spec.max_level.value},
                )
        return SeedReport(inserted=inserted, repaired=repaired)

    # -- metrics and demotion --------------------------------------------

    def record_metric(
        self,
        db: Session,
        action_type: str,
        field: str,
        *,
        count: int = 1,
        tenant_id: UUID | None = None,
    ) -> None:
        get_spec(action_type)
        if field not in _METRIC_FIELDS:
            raise ValueError(f"unknown autonomy metric field: {field!r}")
        if count < 1:
            raise ValueError("count must be >= 1")
        tenant = tenant_id or DEFAULT_TENANT_ID
        start, end = _day_window(self._clock.now())
        values: dict[str, Any] = {
            "id": uuid7(),
            "tenant_id": tenant,
            "action_type": action_type,
            "window_start": start,
            "window_end": end,
            "proposals": 0,
            "approved_unchanged": 0,
            "edited": 0,
            "rejected": 0,
            "incidents": 0,
        }
        values[field] = count
        column = AutonomyMetric.__table__.c[field]
        db.execute(
            pg_insert(AutonomyMetric)
            .values(**values)
            .on_conflict_do_update(
                constraint="uq_autonomy_metrics_window",
                set_={field: column + count},
            )
        )

    def record_incident(
        self,
        db: Session,
        action_type: str,
        *,
        count: int = 1,
        tenant_id: UUID | None = None,
    ) -> None:
        """Other modules call this when an autonomous action caused harm."""

        self.record_metric(db, action_type, "incidents", count=count, tenant_id=tenant_id)

    def demote(self, db: Session, *, tenant_id: UUID | None = None) -> list[Demotion]:
        """Apply the owner-agreed demotion rules once per metric window."""

        tenant = tenant_id or DEFAULT_TENANT_ID
        now = self._clock.now()
        horizon = now - DEMOTION_WINDOW
        metrics = list(
            db.execute(
                select(AutonomyMetric)
                .where(
                    AutonomyMetric.tenant_id == tenant,
                    AutonomyMetric.window_end >= horizon,
                )
                .order_by(AutonomyMetric.window_end, AutonomyMetric.id)
            ).scalars()
        )
        grouped: dict[str, list[AutonomyMetric]] = {}
        for metric in metrics:
            grouped.setdefault(metric.action_type, []).append(metric)

        demotions: list[Demotion] = []
        for action_type in sorted(grouped):
            rows = grouped[action_type]
            spec = CATALOG.get(action_type)
            if spec is None or spec.locked:
                continue
            policy = self.policy_row(db, action_type, tenant_id=tenant)
            if policy is None:
                continue
            try:
                current = AutonomyLevel(policy.level)
            except ValueError:
                continue
            if current.rank <= 0:
                continue

            proposals = sum(row.proposals for row in rows)
            approved = sum(row.approved_unchanged for row in rows)
            incidents = sum(row.incidents for row in rows)
            reason: str | None = None
            if incidents > 0:
                reason = f"{incidents} incident(s) in the last 30 days"
            elif proposals >= DEMOTION_MIN_PROPOSALS and (
                approved / proposals < DEMOTION_MIN_APPROVAL_RATE
            ):
                reason = (
                    f"{approved}/{proposals} proposals approved unchanged "
                    f"(< {int(DEMOTION_MIN_APPROVAL_RATE * 100)}%)"
                )
            if reason is None:
                continue

            latest = rows[-1]
            if not self._claim_demotion(db, tenant, action_type, latest):
                continue

            lowered = current.one_below()
            if lowered.rank > spec.max_level.rank:
                lowered = spec.max_level
            before = {"level": current.value}
            policy.level = lowered.value
            policy.updated_by = None
            db.flush()
            demotions.append(
                Demotion(
                    action_type=action_type,
                    from_level=current,
                    to_level=lowered,
                    reason=reason,
                )
            )
            self._audit.record_change(
                db,
                action="autonomy.demoted",
                actor=Actor.system(),
                subject_type="autonomy_policy",
                subject_id=policy.id,
                before=before,
                after={"level": lowered.value, "reason": reason},
            )
            db.add(
                HumanTask(
                    tenant_id=tenant,
                    task_type="autonomy.demoted",
                    subject_type="autonomy_policy",
                    subject_id=policy.id,
                    title=_title(
                        f"Autonomy demoted: {action_type} {current.value} -> {lowered.value}"
                    ),
                    assignee_role="owner",
                    priority="high",
                )
            )
            self._outbox.enqueue(
                db,
                event_type="autonomy.demoted",
                tenant_id=tenant,
                payload={
                    "action_type": action_type,
                    "from_level": current.value,
                    "to_level": lowered.value,
                    "reason": reason,
                },
            )
        return demotions

    def _claim_demotion(
        self,
        db: Session,
        tenant_id: UUID,
        action_type: str,
        metric: AutonomyMetric,
    ) -> bool:
        key = f"{action_type}:{metric.id}"
        statement = (
            pg_insert(IdempotencyKey)
            .values(id=uuid7(), tenant_id=tenant_id, key=key, scope=_DEMOTION_SCOPE)
            .on_conflict_do_nothing(index_elements=["key", "scope"])
            .returning(IdempotencyKey.id)
        )
        return db.execute(statement).scalar_one_or_none() is not None


class ApprovalService:
    """Creates and decides approval requests."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        audit: AuditWriter | None = None,
        outbox: OutboxWriter | None = None,
        timers: TimerService | None = None,
        autonomy: AutonomyService | None = None,
        ttl: timedelta = timedelta(hours=24),
        escalation: timedelta = timedelta(minutes=30),
    ) -> None:
        if ttl <= timedelta(0):
            raise ValueError("approval ttl must be positive")
        if escalation <= timedelta(0):
            raise ValueError("approval escalation delay must be positive")
        if escalation >= ttl:
            raise ValueError("approval escalation must happen before expiry")
        self._clock = clock or SystemClock()
        self._audit = audit or AuditWriter()
        self._outbox = outbox or OutboxWriter(self._clock)
        self._timers = timers or TimerService(
            clock=self._clock, outbox=self._outbox, audit=self._audit
        )
        self._autonomy = autonomy or AutonomyService(
            clock=self._clock, audit=self._audit, outbox=self._outbox
        )
        self._ttl = ttl
        self._escalation = escalation

    @classmethod
    def from_settings(cls, settings: Settings, *, clock: Clock | None = None) -> ApprovalService:
        return cls(
            clock=clock,
            ttl=timedelta(minutes=settings.approval_ttl_minutes),
            escalation=timedelta(minutes=settings.approval_escalation_minutes),
        )

    @property
    def autonomy(self) -> AutonomyService:
        return self._autonomy

    @property
    def timers(self) -> TimerService:
        return self._timers

    # -- queries ----------------------------------------------------------

    def get(
        self, db: Session, request_id: UUID, *, tenant_id: UUID | None = None
    ) -> ApprovalRequest:
        request = (
            db.execute(
                select(ApprovalRequest).where(
                    ApprovalRequest.id == request_id,
                    ApprovalRequest.tenant_id == (tenant_id or DEFAULT_TENANT_ID),
                )
            )
            .scalars()
            .first()
        )
        if request is None:
            raise ApprovalNotFoundError(str(request_id))
        return request

    def list_requests(
        self,
        db: Session,
        *,
        status: str | None = None,
        tenant_id: UUID | None = None,
        limit: int = 100,
    ) -> Sequence[ApprovalRequest]:
        statement = select(ApprovalRequest).where(
            ApprovalRequest.tenant_id == (tenant_id or DEFAULT_TENANT_ID)
        )
        if status is not None:
            statement = statement.where(ApprovalRequest.status == status)
        statement = statement.order_by(ApprovalRequest.created_at, ApprovalRequest.id).limit(
            max(1, min(limit, 500))
        )
        return list(db.execute(statement).scalars())

    # -- request ----------------------------------------------------------

    def submit(
        self,
        db: Session,
        *,
        action_type: str,
        subject_type: str,
        proposed: Mapping[str, Any],
        requested_by: UUID | None,
        subject_id: UUID | None = None,
        reason: str | None = None,
        value_paise: int = 0,
        context: Mapping[str, Any] | None = None,
        tenant_id: UUID | None = None,
        ip: str | None = None,
    ) -> Submission:
        """Resolve policy and either allow, deny, or open an approval request."""

        tenant = tenant_id or DEFAULT_TENANT_ID
        decision = self._autonomy.decision_for(
            db, action_type, value_paise=value_paise, tenant_id=tenant
        )
        safe_proposed = _as_dict(proposed)
        safe_context = _as_dict(context)
        actor = Actor.user(requested_by) if requested_by is not None else Actor.system()

        if decision.decision is Decision.DENY:
            # Nothing is written: the caller's transaction may continue, and a
            # denied request must not leave a half-created approval behind.
            raise PolicyDeniedError(action_type)

        if decision.decision is Decision.AUTO:
            self._audit.record(
                db,
                action="approval.auto_approved",
                actor=actor,
                subject_type=subject_type,
                subject_id=subject_id,
                after={
                    "action_type": action_type,
                    "level": decision.level.value,
                    "review_after": decision.review_after,
                    "proposed": safe_proposed,
                },
                ip=ip,
            )
            self._outbox.enqueue(
                db,
                event_type="approval.auto_approved",
                tenant_id=tenant,
                payload={
                    "action_type": action_type,
                    "level": decision.level.value,
                    "subject_type": subject_type,
                    "subject_id": subject_id,
                    "review_after": decision.review_after,
                },
            )
            return Submission(decision=decision, approval=None)

        now = self._clock.now()
        expires_at = now + self._ttl
        approvals_needed = 2 if decision.requires_four_eyes else 1
        request = ApprovalRequest(
            tenant_id=tenant,
            action_type=action_type,
            subject_type=subject_type,
            subject_id=subject_id,
            requested_by=requested_by,
            status="pending",
            level=decision.level.value,
            proposed=safe_proposed,
            context=safe_context,
            requires_four_eyes=decision.requires_four_eyes,
            approvals_needed=approvals_needed,
            approvals_count=0,
            reason=reason,
            expires_at=expires_at,
        )
        db.add(request)
        db.flush()
        db.refresh(request)

        self._timers.schedule(
            db,
            name=TimerKind.APPROVAL_ESCALATION,
            subject_type=_APPROVAL_SUBJECT,
            subject_id=request.id,
            at=now + self._escalation,
            payload={"approval_id": str(request.id)},
            tenant_id=tenant,
        )
        self._timers.schedule(
            db,
            name=TimerKind.APPROVAL_EXPIRY,
            subject_type=_APPROVAL_SUBJECT,
            subject_id=request.id,
            at=expires_at,
            payload={"approval_id": str(request.id)},
            tenant_id=tenant,
        )
        db.add(
            HumanTask(
                tenant_id=tenant,
                task_type="approval.requested",
                subject_type=_APPROVAL_SUBJECT,
                subject_id=request.id,
                title=_title(
                    f"Approve {action_type} ({subject_type}"
                    + (f" {subject_id}" if subject_id is not None else "")
                    + ")"
                ),
                assignee_role="owner" if decision.owner_only else "agent",
                due_at=expires_at,
                priority="high" if decision.owner_only or decision.requires_four_eyes else "normal",
            )
        )
        self._audit.record(
            db,
            action="approval.requested",
            actor=actor,
            subject_type=_APPROVAL_SUBJECT,
            subject_id=request.id,
            after={
                "action_type": action_type,
                "level": decision.level.value,
                "status": request.status,
                "proposed": safe_proposed,
                "requires_four_eyes": request.requires_four_eyes,
                "approvals_needed": approvals_needed,
                "expires_at": expires_at,
            },
            ip=ip,
        )
        self._outbox.enqueue(
            db,
            event_type="approval.requested",
            tenant_id=tenant,
            payload={
                "approval_id": str(request.id),
                "action_type": action_type,
                "subject_type": subject_type,
                "subject_id": subject_id,
                "level": decision.level.value,
                "requires_four_eyes": request.requires_four_eyes,
            },
        )
        self._autonomy.record_metric(db, action_type, "proposals", tenant_id=tenant)
        return Submission(decision=decision, approval=request)

    # -- decisions --------------------------------------------------------

    def decide(
        self,
        db: Session,
        *,
        request_id: UUID,
        reviewer_id: UUID,
        reviewer_role: str,
        decision: ReviewDecision | str,
        final: Mapping[str, Any] | None = None,
        reason: str | None = None,
        tenant_id: UUID | None = None,
        ip: str | None = None,
    ) -> ApprovalRequest:
        tenant = tenant_id or DEFAULT_TENANT_ID
        request = (
            db.execute(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.id == request_id,
                    ApprovalRequest.tenant_id == tenant,
                )
                .with_for_update()
            )
            .scalars()
            .first()
        )
        if request is None:
            raise ApprovalNotFoundError(str(request_id))
        if request.status != "pending":
            raise ApprovalStateError(str(request_id), request.status)
        if request.requested_by is not None and request.requested_by == reviewer_id:
            raise SelfApprovalError("a requester cannot decide their own request")

        spec = get_spec(request.action_type)
        if spec.owner_only and reviewer_role != "owner":
            raise ApprovalPermissionError(request.action_type)

        parsed = ReviewDecision(decision)
        if parsed is ReviewDecision.APPROVE:
            return self._approve(db, request, reviewer_id, tenant, ip)
        if parsed is ReviewDecision.EDIT:
            if final is None:
                raise EditRequiresChangeError("an edit decision must supply the final payload")
            safe_final = _as_dict(final)
            if safe_final == request.proposed:
                raise EditRequiresChangeError("the edited payload must differ from the proposal")
            return self._finish(
                db,
                request,
                status="edited",
                reviewer_id=reviewer_id,
                final=safe_final,
                reason=(reason.strip() if reason and reason.strip() else None),
                tenant=tenant,
                ip=ip,
                metric="edited",
            )
        reason_text = reason.strip() if reason else ""
        if not reason_text:
            raise ReasonRequiredError("a rejection requires a reason")
        return self._finish(
            db,
            request,
            status="rejected",
            reviewer_id=reviewer_id,
            final=None,
            reason=reason_text,
            tenant=tenant,
            ip=ip,
            metric="rejected",
        )

    def _approve(
        self,
        db: Session,
        request: ApprovalRequest,
        reviewer_id: UUID,
        tenant: UUID,
        ip: str | None,
    ) -> ApprovalRequest:
        if request.approvals_needed == 2 and request.approvals_count == 0:
            request.reviewer_id = reviewer_id
            request.approvals_count = 1
            request.decided_at = self._clock.now()
            db.flush()
            self._audit.record(
                db,
                action="approval.first_approval",
                actor=Actor.user(reviewer_id),
                subject_type=_APPROVAL_SUBJECT,
                subject_id=request.id,
                after={
                    "status": request.status,
                    "reviewer_id": reviewer_id,
                    "approvals_count": 1,
                    "approvals_needed": 2,
                },
                ip=ip,
            )
            self._outbox.enqueue(
                db,
                event_type="approval.first_approval",
                tenant_id=tenant,
                payload={
                    "approval_id": str(request.id),
                    "action_type": request.action_type,
                    "reviewer_id": str(reviewer_id),
                },
            )
            return request

        if request.approvals_needed == 2 and request.reviewer_id == reviewer_id:
            raise DistinctReviewerError("the second approval must come from a different reviewer")
        return self._finish(
            db,
            request,
            status="approved",
            reviewer_id=reviewer_id,
            final=dict(request.proposed),
            reason=None,
            tenant=tenant,
            ip=ip,
            metric="approved_unchanged",
        )

    def _finish(
        self,
        db: Session,
        request: ApprovalRequest,
        *,
        status: str,
        reviewer_id: UUID,
        final: dict[str, Any] | None,
        reason: str | None,
        tenant: UUID,
        ip: str | None,
        metric: str,
    ) -> ApprovalRequest:
        before = _snapshot(request)
        if request.approvals_needed == 2 and request.approvals_count == 1:
            request.second_reviewer_id = reviewer_id
            request.approvals_count = 2
        else:
            request.reviewer_id = reviewer_id
            request.approvals_count = request.approvals_needed
        request.status = status
        request.final = final
        request.reason = reason
        request.decided_at = self._clock.now()
        db.flush()

        self._cancel_timers(db, request, tenant)
        after = _snapshot(request)
        self._audit.record_change(
            db,
            action="approval.decided",
            actor=Actor.user(reviewer_id),
            subject_type=_APPROVAL_SUBJECT,
            subject_id=request.id,
            before=before,
            after=after,
            ip=ip,
        )
        self._outbox.enqueue(
            db,
            event_type="approval.decided",
            tenant_id=tenant,
            payload={
                "approval_id": str(request.id),
                "action_type": request.action_type,
                "status": status,
                "final": final,
                "reason": reason,
                "reviewer_id": str(reviewer_id),
            },
        )
        self._autonomy.record_metric(db, request.action_type, metric, tenant_id=tenant)
        return request

    def _cancel_timers(self, db: Session, request: ApprovalRequest, tenant: UUID) -> None:
        del tenant  # the timer is keyed by subject; tenant is implicit
        for name in (TimerKind.APPROVAL_ESCALATION, TimerKind.APPROVAL_EXPIRY):
            self._timers.cancel(
                db,
                name=name,
                subject_type=_APPROVAL_SUBJECT,
                subject_id=request.id,
            )

    # -- timers -----------------------------------------------------------

    def escalate(
        self,
        db: Session,
        request_id: UUID,
        *,
        tenant_id: UUID | None = None,
        ip: str | None = None,
    ) -> ApprovalRequest:
        tenant = tenant_id or DEFAULT_TENANT_ID
        request = (
            db.execute(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.id == request_id,
                    ApprovalRequest.tenant_id == tenant,
                )
                .with_for_update()
            )
            .scalars()
            .first()
        )
        if request is None:
            raise ApprovalNotFoundError(str(request_id))
        if request.status != "pending" or request.escalated_at is not None:
            return request

        now = self._clock.now()
        request.escalated_at = now
        request.escalation_count += 1
        db.flush()
        db.add(
            HumanTask(
                tenant_id=tenant,
                task_type="approval.escalated",
                subject_type=_APPROVAL_SUBJECT,
                subject_id=request.id,
                title=_title(
                    f"Escalated approval: {request.action_type} still pending after "
                    f"{self._escalation}"
                ),
                assignee_role="owner",
                due_at=request.expires_at,
                priority="urgent",
            )
        )
        self._audit.record(
            db,
            action="approval.escalated",
            actor=Actor.system(),
            subject_type=_APPROVAL_SUBJECT,
            subject_id=request.id,
            before={"escalated_at": None},
            after={
                "escalated_at": now,
                "escalation_count": request.escalation_count,
                "assignee_role": "owner",
            },
            ip=ip,
        )
        self._outbox.enqueue(
            db,
            event_type="approval.escalated",
            tenant_id=tenant,
            payload={
                "approval_id": str(request.id),
                "action_type": request.action_type,
                "escalation_count": request.escalation_count,
            },
        )
        return request

    def expire(
        self,
        db: Session,
        request_id: UUID,
        *,
        tenant_id: UUID | None = None,
        ip: str | None = None,
    ) -> ApprovalRequest:
        tenant = tenant_id or DEFAULT_TENANT_ID
        request = (
            db.execute(
                select(ApprovalRequest)
                .where(
                    ApprovalRequest.id == request_id,
                    ApprovalRequest.tenant_id == tenant,
                )
                .with_for_update()
            )
            .scalars()
            .first()
        )
        if request is None:
            raise ApprovalNotFoundError(str(request_id))
        if request.status != "pending":
            return request

        before = _snapshot(request)
        request.status = "expired"
        request.decided_at = self._clock.now()
        request.reason = request.reason or "expired without a decision"
        db.flush()
        self._cancel_timers(db, request, tenant)
        db.add(
            HumanTask(
                tenant_id=tenant,
                task_type="approval.expired",
                subject_type=_APPROVAL_SUBJECT,
                subject_id=request.id,
                title=_title(f"Approval expired without a decision: {request.action_type}"),
                assignee_role="owner",
                priority="urgent",
            )
        )
        self._audit.record_change(
            db,
            action="approval.expired",
            actor=Actor.system(),
            subject_type=_APPROVAL_SUBJECT,
            subject_id=request.id,
            before=before,
            after=_snapshot(request),
            ip=ip,
        )
        self._outbox.enqueue(
            db,
            event_type="approval.expired",
            tenant_id=tenant,
            payload={
                "approval_id": str(request.id),
                "action_type": request.action_type,
                "expires_at": request.expires_at,
            },
        )
        self._autonomy.record_metric(db, request.action_type, "rejected", tenant_id=tenant)
        return request

    def handle_timer(self, db: Session, payload: Mapping[str, Any]) -> str | None:
        """Route one ``timer.fired`` payload. Unknown timer names are not ours."""

        name = str(payload.get("name", ""))
        if name not in {TimerKind.APPROVAL_ESCALATION.value, TimerKind.APPROVAL_EXPIRY.value}:
            raise UnhandledTimerError(name)
        raw_id = payload.get("subject_id")
        if raw_id is None:
            nested = payload.get("payload") or {}
            if isinstance(nested, Mapping):
                raw_id = nested.get("approval_id")
        if raw_id is None:
            raise UnhandledTimerError(name)
        request_id = UUID(str(raw_id))
        if name == TimerKind.APPROVAL_ESCALATION.value:
            self.escalate(db, request_id)
            return "escalated"
        self.expire(db, request_id)
        return "expired"
