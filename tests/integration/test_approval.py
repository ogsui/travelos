"""Approval lifecycle, four-eyes, timers, seeding and autonomy demotion."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.approval.errors import (
    ApprovalPermissionError,
    ApprovalStateError,
    DistinctReviewerError,
    EditRequiresChangeError,
    PolicyDeniedError,
    PolicyViolationError,
    ReasonRequiredError,
    SelfApprovalError,
)
from travelos.approval.matrix import AutonomyLevel
from travelos.approval.models import ApprovalRequest
from travelos.approval.service import ApprovalService, AutonomyService
from travelos.core.clock import ManualClock
from travelos.core.models import (
    AuditLog,
    AutonomyMetric,
    AutonomyPolicy,
    HumanTask,
    OutboxEvent,
    User,
)
from travelos.workflow.models import WorkflowTimer
from travelos.workflow.timers import TimerKind

pytestmark = pytest.mark.integration

START = datetime(2026, 5, 1, 9, 0, tzinfo=UTC)
TTL = timedelta(hours=24)
ESCALATION = timedelta(minutes=30)


def make_user(db: Session, role: str = "agent", email: str | None = None) -> User:
    user = User(
        email=email or f"{role}-{uuid4().hex[:8]}@example.test",
        password_hash="synthetic-hash",
        role=role,
    )
    db.add(user)
    db.flush()
    return user


def service(clock: ManualClock | None = None) -> ApprovalService:
    return ApprovalService(
        clock=clock or ManualClock(START),
        ttl=TTL,
        escalation=ESCALATION,
    )


def actions(db: Session) -> list[str]:
    return list(db.execute(select(AuditLog.action).order_by(AuditLog.at)).scalars())


def timers(db: Session) -> dict[str, WorkflowTimer]:
    return {timer.name: timer for timer in db.execute(select(WorkflowTimer)).scalars()}


def events(db: Session, event_type: str) -> list[OutboxEvent]:
    return list(
        db.execute(select(OutboxEvent).where(OutboxEvent.event_type == event_type)).scalars()
    )


# --------------------------------------------------------------------------
# Submit / resolve
# --------------------------------------------------------------------------


def test_a1_submit_creates_a_pending_request_with_timers_task_event_and_metric(
    db: Session,
) -> None:
    requester = make_user(db)
    approval = service()

    submission = approval.submit(
        db,
        action_type="quote.send",
        subject_type="quote",
        subject_id=uuid4(),
        proposed={"total_paise": 12_500_000, "currency": "INR"},
        requested_by=requester.id,
    )
    db.flush()

    request = submission.approval
    assert request is not None
    assert request.status == "pending"
    assert request.level == "A1"
    assert request.approvals_needed == 1
    assert request.requires_four_eyes is False
    assert request.proposed == {"total_paise": 12_500_000, "currency": "INR"}
    assert request.expires_at == START + TTL

    scheduled = timers(db)
    assert set(scheduled) == {
        TimerKind.APPROVAL_ESCALATION.value,
        TimerKind.APPROVAL_EXPIRY.value,
    }
    assert scheduled[TimerKind.APPROVAL_ESCALATION.value].due_at == START + ESCALATION
    assert scheduled[TimerKind.APPROVAL_EXPIRY.value].due_at == START + TTL

    task = db.execute(select(HumanTask)).scalar_one()
    assert task.task_type == "approval.requested"
    assert task.assignee_role == "agent"
    assert task.subject_id == request.id

    requested = events(db, "approval.requested")
    assert len(requested) == 1
    assert requested[0].payload["approval_id"] == str(request.id)

    metric = db.execute(select(AutonomyMetric)).scalar_one()
    assert metric.action_type == "quote.send"
    assert metric.proposals == 1

    assert "approval.requested" in actions(db)
    assert "workflow.timer_scheduled" in actions(db)


def test_a3_auto_action_creates_no_request_but_is_audited(db: Session) -> None:
    requester = make_user(db)
    approval = service()

    submission = approval.submit(
        db,
        action_type="crm.capture_lead",
        subject_type="lead",
        subject_id=uuid4(),
        proposed={"name": "Synthetic Lead"},
        requested_by=requester.id,
    )
    db.flush()

    assert submission.decision.decision.value == "auto"
    assert submission.approval is None
    assert db.execute(select(ApprovalRequest)).scalars().all() == []
    assert "approval.auto_approved" in actions(db)
    assert len(events(db, "approval.auto_approved")) == 1


def test_a0_action_is_denied_and_writes_nothing(db: Session) -> None:
    requester = make_user(db)
    approval = service()

    with pytest.raises(PolicyDeniedError):
        approval.submit(
            db,
            action_type="payment.supplier_payment",
            subject_type="supplier_payment",
            proposed={"amount_paise": 10_000_000},
            requested_by=requester.id,
        )
    db.flush()

    assert db.execute(select(ApprovalRequest)).scalars().all() == []
    assert list(db.execute(select(OutboxEvent)).scalars()) == []


# --------------------------------------------------------------------------
# Decisions
# --------------------------------------------------------------------------


def test_requester_cannot_approve_their_own_request(db: Session) -> None:
    requester = make_user(db)
    approval = service()
    submission = approval.submit(
        db,
        action_type="quote.send",
        subject_type="quote",
        proposed={"total_paise": 1},
        requested_by=requester.id,
    )
    assert submission.approval is not None

    with pytest.raises(SelfApprovalError):
        approval.decide(
            db,
            request_id=submission.approval.id,
            reviewer_id=requester.id,
            reviewer_role="agent",
            decision="approve",
        )


def test_approve_sets_final_cancels_timers_and_audits_before_after(db: Session) -> None:
    requester = make_user(db)
    reviewer = make_user(db)
    approval = service()
    submission = approval.submit(
        db,
        action_type="quote.send",
        subject_type="quote",
        proposed={"total_paise": 5_000},
        requested_by=requester.id,
    )
    assert submission.approval is not None

    decided = approval.decide(
        db,
        request_id=submission.approval.id,
        reviewer_id=reviewer.id,
        reviewer_role="agent",
        decision="approve",
    )
    db.flush()

    assert decided.status == "approved"
    assert decided.final == {"total_paise": 5_000}
    assert decided.reviewer_id == reviewer.id
    assert decided.approvals_count == 1
    assert all(timer.status == "cancelled" for timer in timers(db).values())

    row = db.execute(select(AuditLog).where(AuditLog.action == "approval.decided")).scalar_one()
    assert row.actor_id == reviewer.id
    assert row.before is not None and row.before["status"] == "pending"
    assert row.after is not None and row.after["status"] == "approved"

    metric = db.execute(select(AutonomyMetric)).scalar_one()
    assert metric.approved_unchanged == 1


def test_edit_records_the_final_diff_and_reject_requires_a_reason(db: Session) -> None:
    requester = make_user(db)
    reviewer = make_user(db)
    approval = service()

    first = approval.submit(
        db,
        action_type="quote.send",
        subject_type="quote",
        proposed={"total_paise": 5_000},
        requested_by=requester.id,
    )
    assert first.approval is not None
    edited = approval.decide(
        db,
        request_id=first.approval.id,
        reviewer_id=reviewer.id,
        reviewer_role="agent",
        decision="edit",
        final={"total_paise": 4_900},
    )
    assert edited.status == "edited"
    assert edited.final == {"total_paise": 4_900}

    second = approval.submit(
        db,
        action_type="quote.send",
        subject_type="quote",
        proposed={"total_paise": 6_000},
        requested_by=requester.id,
    )
    assert second.approval is not None
    with pytest.raises(ReasonRequiredError):
        approval.decide(
            db,
            request_id=second.approval.id,
            reviewer_id=reviewer.id,
            reviewer_role="agent",
            decision="reject",
        )
    rejected = approval.decide(
        db,
        request_id=second.approval.id,
        reviewer_id=reviewer.id,
        reviewer_role="agent",
        decision="reject",
        reason="fare rules not acceptable",
    )
    db.flush()
    assert rejected.status == "rejected"
    assert rejected.reason == "fare rules not acceptable"


def test_edit_must_change_the_payload(db: Session) -> None:
    requester = make_user(db)
    reviewer = make_user(db)
    approval = service()
    submission = approval.submit(
        db,
        action_type="quote.send",
        subject_type="quote",
        proposed={"total_paise": 1},
        requested_by=requester.id,
    )
    assert submission.approval is not None
    with pytest.raises(EditRequiresChangeError):
        approval.decide(
            db,
            request_id=submission.approval.id,
            reviewer_id=reviewer.id,
            reviewer_role="agent",
            decision="edit",
            final={"total_paise": 1},
        )


def test_deciding_a_finished_request_is_refused(db: Session) -> None:
    requester = make_user(db)
    reviewer = make_user(db)
    approval = service()
    submission = approval.submit(
        db,
        action_type="quote.send",
        subject_type="quote",
        proposed={"total_paise": 1},
        requested_by=requester.id,
    )
    assert submission.approval is not None
    approval.decide(
        db,
        request_id=submission.approval.id,
        reviewer_id=reviewer.id,
        reviewer_role="agent",
        decision="approve",
    )
    with pytest.raises(ApprovalStateError):
        approval.decide(
            db,
            request_id=submission.approval.id,
            reviewer_id=reviewer.id,
            reviewer_role="agent",
            decision="approve",
        )


def test_four_eyes_threshold_requires_two_distinct_reviewers(db: Session) -> None:
    owner = make_user(db, "owner")
    requester = make_user(db)
    first_reviewer = make_user(db)
    second_reviewer = make_user(db)
    approval = service()
    approval.autonomy.update_policy(
        db,
        action_type="quote.send",
        level="A1",
        value_threshold_paise=500_000,
        actor_id=owner.id,
    )

    submission = approval.submit(
        db,
        action_type="quote.send",
        subject_type="quote",
        proposed={"total_paise": 750_000},
        requested_by=requester.id,
        value_paise=750_000,
    )
    request = submission.approval
    assert request is not None
    assert request.requires_four_eyes is True
    assert request.approvals_needed == 2

    first = approval.decide(
        db,
        request_id=request.id,
        reviewer_id=first_reviewer.id,
        reviewer_role="agent",
        decision="approve",
    )
    assert first.status == "pending"
    assert first.approvals_count == 1
    assert first.reviewer_id == first_reviewer.id

    with pytest.raises(DistinctReviewerError):
        approval.decide(
            db,
            request_id=request.id,
            reviewer_id=first_reviewer.id,
            reviewer_role="agent",
            decision="approve",
        )

    final = approval.decide(
        db,
        request_id=request.id,
        reviewer_id=second_reviewer.id,
        reviewer_role="agent",
        decision="approve",
    )
    db.flush()
    assert final.status == "approved"
    assert final.approvals_count == 2
    assert final.second_reviewer_id == second_reviewer.id


def test_owner_only_actions_reject_non_owner_reviewers(db: Session) -> None:
    requester = make_user(db)
    reviewer = make_user(db)
    owner = make_user(db, "owner")
    approval = service()
    submission = approval.submit(
        db,
        action_type="privacy.customer_delete",
        subject_type="customer",
        proposed={"customer_id": str(uuid4())},
        requested_by=requester.id,
    )
    assert submission.approval is not None

    with pytest.raises(ApprovalPermissionError):
        approval.decide(
            db,
            request_id=submission.approval.id,
            reviewer_id=reviewer.id,
            reviewer_role="agent",
            decision="approve",
        )

    decided = approval.decide(
        db,
        request_id=submission.approval.id,
        reviewer_id=owner.id,
        reviewer_role="owner",
        decision="approve",
    )
    assert decided.status == "approved"


# --------------------------------------------------------------------------
# Timers, escalation and expiry
# --------------------------------------------------------------------------


def test_unanswered_request_escalates_then_expires_with_human_tasks(db: Session) -> None:
    requester = make_user(db)
    clock = ManualClock(START)
    approval = service(clock)
    submission = approval.submit(
        db,
        action_type="quote.send",
        subject_type="quote",
        proposed={"total_paise": 1},
        requested_by=requester.id,
    )
    request = submission.approval
    assert request is not None

    clock.advance(ESCALATION)
    assert approval.timers.fire_due(db) == 1
    escalation_event = db.execute(
        select(OutboxEvent).where(OutboxEvent.event_type == "timer.fired")
    ).scalar_one()
    assert approval.handle_timer(db, escalation_event.payload) == "escalated"
    db.flush()

    assert request.escalated_at == clock.now()
    assert request.escalation_count == 1
    escalated = db.execute(
        select(HumanTask).where(HumanTask.task_type == "approval.escalated")
    ).scalar_one()
    assert escalated.assignee_role == "owner"
    assert escalated.priority == "urgent"
    assert "approval.escalated" in actions(db)

    clock.set(START + TTL)
    assert approval.timers.fire_due(db) == 1
    expiry_event = db.execute(
        select(OutboxEvent).where(
            OutboxEvent.event_type == "timer.fired",
            OutboxEvent.payload["name"].astext == TimerKind.APPROVAL_EXPIRY.value,
        )
    ).scalar_one()
    assert approval.handle_timer(db, expiry_event.payload) == "expired"
    db.flush()

    assert request.status == "expired"
    expired = db.execute(
        select(HumanTask).where(HumanTask.task_type == "approval.expired")
    ).scalar_one()
    assert expired.assignee_role == "owner"
    assert expired.priority == "urgent"
    assert len(events(db, "approval.expired")) == 1

    # Timer redelivery is harmless: no second task or event.
    approval.handle_timer(db, expiry_event.payload)
    db.flush()
    assert len(db.execute(select(HumanTask)).scalars().all()) == 3
    assert len(events(db, "approval.expired")) == 1


def test_approval_timer_handler_refuses_foreign_timer_names(db: Session) -> None:
    from travelos.approval.errors import UnhandledTimerError

    approval = service()
    with pytest.raises(UnhandledTimerError):
        approval.handle_timer(db, {"name": "quote.expiry", "subject_id": str(uuid4())})


# --------------------------------------------------------------------------
# Owner policy updates and seed
# --------------------------------------------------------------------------


def test_only_owner_role_may_update_policy_in_the_service(db: Session) -> None:
    agent = make_user(db)
    approval = service()
    with pytest.raises(ApprovalPermissionError):
        approval.autonomy.update_policy(
            db,
            action_type="quote.send",
            level="A2",
            actor_id=agent.id,
            actor_role="agent",
        )


def test_locked_policies_cannot_be_raised_or_unlocked(db: Session) -> None:
    owner = make_user(db, "owner")
    autonomy = AutonomyService()
    with pytest.raises(PolicyViolationError, match="LOCKED"):
        autonomy.update_policy(
            db,
            action_type="booking.confirm",
            level="A3",
            actor_id=owner.id,
        )
    with pytest.raises(PolicyViolationError, match="LOCKED"):
        autonomy.update_policy(
            db,
            action_type="quote.below_floor",
            level="A1",
            actor_id=owner.id,
        )
    db.flush()

    assert db.execute(select(AutonomyPolicy)).scalars().all() == []


def test_update_policy_is_audited_before_and_after(db: Session) -> None:
    owner = make_user(db, "owner")
    autonomy = AutonomyService()
    autonomy.update_policy(
        db,
        action_type="quote.send",
        level="A1",
        value_threshold_paise=0,
        actor_id=owner.id,
    )
    autonomy.update_policy(
        db,
        action_type="quote.send",
        level="A2",
        value_threshold_paise=700_000,
        actor_id=owner.id,
    )
    db.flush()

    rows = list(
        db.execute(select(AuditLog).where(AuditLog.action == "autonomy.policy_changed")).scalars()
    )
    assert len(rows) == 2
    assert rows[1].before is not None and rows[1].before["level"] == "A1"
    assert rows[1].after is not None and rows[1].after["level"] == "A2"
    assert len(events(db, "autonomy.policy_changed")) == 2


def test_seed_inserts_all_rows_and_repairs_tampered_ones_idempotently(db: Session) -> None:
    db.add(AutonomyPolicy(action_type="quote.below_floor", level="A3"))
    db.add(AutonomyPolicy(action_type="quote.send", level="A3"))
    db.add(AutonomyPolicy(action_type="crm.capture_lead", level="A1"))
    db.flush()

    autonomy = AutonomyService()
    first = autonomy.seed(db)
    db.flush()
    assert first.inserted >= 40
    assert first.repaired == 2

    by_action = {row.action_type: row for row in db.execute(select(AutonomyPolicy)).scalars()}
    assert by_action["quote.below_floor"].level == "A0"
    assert by_action["quote.send"].level == "A2"
    assert by_action["crm.capture_lead"].level == "A1"  # safe owner change preserved

    second = autonomy.seed(db)
    assert second.inserted == 0
    assert second.repaired == 0
    assert (
        len(
            list(
                db.execute(
                    select(AuditLog).where(AuditLog.action == "autonomy.policy_seed_repaired")
                ).scalars()
            )
        )
        == 2
    )


# --------------------------------------------------------------------------
# Auto-demotion
# --------------------------------------------------------------------------


def test_incident_demotes_one_level_once_per_metric_window(db: Session) -> None:
    owner = make_user(db, "owner")
    autonomy = AutonomyService(clock=ManualClock(START))
    autonomy.update_policy(db, action_type="crm.capture_lead", level="A3", actor_id=owner.id)
    autonomy.record_incident(db, "crm.capture_lead")
    db.flush()

    demotions = autonomy.demote(db)
    db.flush()
    assert len(demotions) == 1
    assert demotions[0].from_level is AutonomyLevel.A3
    assert demotions[0].to_level is AutonomyLevel.A2

    policy = autonomy.policy_row(db, "crm.capture_lead")
    assert policy is not None and policy.level == "A2"
    task = db.execute(
        select(HumanTask).where(HumanTask.task_type == "autonomy.demoted")
    ).scalar_one()
    assert task.assignee_role == "owner"
    assert task.priority == "high"
    assert len(events(db, "autonomy.demoted")) == 1
    assert "autonomy.demoted" in actions(db)

    assert autonomy.demote(db) == []


def test_low_approval_rate_demotes_after_fifty_proposals(db: Session) -> None:
    owner = make_user(db, "owner")
    autonomy = AutonomyService(clock=ManualClock(START))
    autonomy.update_policy(db, action_type="analytics.digest", level="A3", actor_id=owner.id)
    autonomy.record_metric(db, "analytics.digest", "proposals", count=50)
    autonomy.record_metric(db, "analytics.digest", "approved_unchanged", count=40)
    db.flush()

    demotions = autonomy.demote(db)
    assert len(demotions) == 1
    assert demotions[0].to_level is AutonomyLevel.A2
    assert "85%" in demotions[0].reason or "40/50" in demotions[0].reason


def test_demotion_does_not_trigger_below_thresholds_or_for_locked_actions(db: Session) -> None:
    owner = make_user(db, "owner")
    autonomy = AutonomyService(clock=ManualClock(START))
    autonomy.update_policy(db, action_type="analytics.digest", level="A3", actor_id=owner.id)
    autonomy.record_metric(db, "analytics.digest", "proposals", count=49)
    autonomy.record_metric(db, "analytics.digest", "approved_unchanged", count=1)
    db.flush()
    assert autonomy.demote(db) == []

    # LOCKED actions are never demoted or touched by the metric hook.
    autonomy.record_incident(db, "payment.supplier_payment")
    db.flush()
    assert autonomy.demote(db) == []
    assert autonomy.policy_row(db, "payment.supplier_payment") is None


def test_metrics_outside_the_window_are_ignored(db: Session) -> None:
    owner = make_user(db, "owner")
    autonomy = AutonomyService(clock=ManualClock(START))
    autonomy.update_policy(db, action_type="crm.capture_lead", level="A3", actor_id=owner.id)
    db.add(
        AutonomyMetric(
            action_type="crm.capture_lead",
            window_start=START - timedelta(days=60),
            window_end=START - timedelta(days=31),
            incidents=5,
        )
    )
    db.flush()
    assert autonomy.demote(db) == []


def test_requested_by_accepts_none_for_system_requests(db: Session) -> None:
    approval = service()
    submission = approval.submit(
        db,
        action_type="quote.send",
        subject_type="quote",
        proposed={"total_paise": 1},
        requested_by=None,
    )
    assert submission.approval is not None
    assert submission.approval.requested_by is None
