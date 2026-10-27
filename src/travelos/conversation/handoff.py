"""Human handoff for sales conversations (task 011b).

A handoff creates one open ``human_tasks`` row for the inquiry plus a
``conversation_handoffs`` row that holds the reason codes and an encrypted
summary. There is no sender and no outbound channel: the task queue in the
dashboard is the destination, as the owner chose.

Repeated calls are idempotent while a task is open. If a later message adds a
new reason (for example a visa question followed by a refund request), the same
task is refreshed and re-routed rather than duplicated.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Final
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from travelos.channels.models import Conversation, Message
from travelos.conversation.errors import SalesNotFoundError, SalesValidationError
from travelos.conversation.models import ConversationHandoff
from travelos.conversation.summary import (
    MAX_SUMMARY_CHARS,
    SUMMARY_VERSION,
    ConversationFacts,
    build_summary,
)
from travelos.conversation.triggers import (
    TRIGGER_LABELS,
    HandoffPolicy,
    HandoffTrigger,
    TriggerEvidence,
    detect_triggers,
)
from travelos.core.audit import Actor, AuditWriter
from travelos.core.clock import Clock, SystemClock
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.models import HumanTask
from travelos.crm.models import Contact, Customer
from travelos.inquiry.models import Inquiry, MessageDraft, RequirementRow
from travelos.inquiry.requirement import Requirement

_TASK_TYPE: Final = "conversation.handoff"
_OPEN_STATES: Final = ("open", "in_progress")
_MAX_RECENT_INBOUND: Final = 20
_MAX_LOOP_MESSAGES: Final = 200


@dataclass(frozen=True, slots=True)
class HandoffAssessment:
    """Why (and whether) a conversation should go to a human."""

    inquiry_id: UUID
    conversation_id: UUID | None
    triggers: tuple[TriggerEvidence, ...]
    summary: str
    priority: str
    assignee_role: str

    @property
    def triggered(self) -> bool:
        return bool(self.triggers)

    @property
    def codes(self) -> tuple[HandoffTrigger, ...]:
        return tuple(item.trigger for item in self.triggers)


@dataclass(frozen=True, slots=True)
class HandoffResult:
    """The assessment plus what was written (if anything)."""

    assessment: HandoffAssessment
    task: HumanTask | None
    record: ConversationHandoff | None
    created: bool
    refreshed: bool


def _validated_booking_value(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise SalesValidationError("booking value must be a non-negative integer number of paise")
    return value


def _missing_labels(row: RequirementRow) -> tuple[str, ...]:
    labels: list[str] = []
    for item in row.missing_fields:
        field = item.get("field")
        severity = item.get("severity")
        if isinstance(field, str) and field:
            labels.append(f"{field} ({severity})" if isinstance(severity, str) else field)
    return tuple(labels)


def _title(triggers: Sequence[HandoffTrigger]) -> str:
    labels = ", ".join(TRIGGER_LABELS[trigger] for trigger in triggers)
    return f"Handoff: {labels}"[:200]


def _merged_triggers(
    existing: Iterable[str], current: Iterable[HandoffTrigger]
) -> tuple[HandoffTrigger, ...]:
    values = {trigger.value for trigger in current}
    values.update(existing)
    return tuple(trigger for trigger in HandoffTrigger if trigger.value in values)


def _append_trigger_note(summary: str, added: Sequence[HandoffTrigger]) -> str:
    labels = ", ".join(TRIGGER_LABELS[trigger] for trigger in added)
    note = f"New trigger(s) since the first handoff: {labels}"
    combined = f"{summary}\n{note}"
    if len(combined) <= MAX_SUMMARY_CHARS:
        return combined
    return combined[: MAX_SUMMARY_CHARS - len(note) - 1].rstrip() + "\n" + note


class ConversationHandoffService:
    """Assess a conversation and open or refresh a human task. Never sends."""

    def __init__(
        self,
        *,
        clock: Clock | None = None,
        audit: AuditWriter | None = None,
        policy: HandoffPolicy | None = None,
    ) -> None:
        self._clock = clock or SystemClock()
        self._audit = audit or AuditWriter()
        self._policy = policy or HandoffPolicy()

    # -- public API -------------------------------------------------------

    def assess(
        self,
        db: Session,
        *,
        inquiry_id: UUID,
        booking_value_paise: int | None = None,
        tenant_id: UUID = DEFAULT_TENANT_ID,
    ) -> HandoffAssessment:
        """Read the inquiry and decide whether a human is needed. Writes nothing."""

        now = self._clock.now()
        booking_value = _validated_booking_value(booking_value_paise)
        inquiry = self._inquiry(db, inquiry_id, tenant_id)
        conversation = self._conversation(db, inquiry, tenant_id)
        facts, texts, loop_messages = self._facts(db, inquiry, conversation, tenant_id, now)
        stated_budget = (
            facts.requirement.budget_paise
            if facts.requirement is not None and self._policy.include_stated_budget
            else None
        )
        triggers = detect_triggers(
            texts=texts,
            loop_messages=loop_messages,
            now=now,
            booking_value_paise=booking_value,
            stated_budget_paise=stated_budget,
            policy=self._policy,
        )
        return HandoffAssessment(
            inquiry_id=inquiry.id,
            conversation_id=inquiry.conversation_id,
            triggers=triggers,
            summary=build_summary(facts, triggers),
            priority=self._policy.priority(item.trigger for item in triggers),
            assignee_role=self._policy.assignee_role(item.trigger for item in triggers),
        )

    def handoff(
        self,
        db: Session,
        *,
        inquiry_id: UUID,
        requested_by: UUID | None = None,
        booking_value_paise: int | None = None,
        tenant_id: UUID = DEFAULT_TENANT_ID,
    ) -> HandoffResult:
        """Create or refresh the human task for an inquiry."""

        assessment = self.assess(
            db,
            inquiry_id=inquiry_id,
            booking_value_paise=booking_value_paise,
            tenant_id=tenant_id,
        )
        if not assessment.triggered:
            return HandoffResult(
                assessment=assessment,
                task=None,
                record=None,
                created=False,
                refreshed=False,
            )

        actor = Actor.user(requested_by) if requested_by is not None else Actor.system()
        task = self._open_task(db, inquiry_id, tenant_id)
        if task is None:
            task = HumanTask(
                tenant_id=tenant_id,
                task_type=_TASK_TYPE,
                subject_type="inquiry",
                subject_id=inquiry_id,
                title=_title(assessment.codes),
                assignee_role=assessment.assignee_role,
                priority=assessment.priority,
            )
            db.add(task)
            db.flush()
            created_record = ConversationHandoff(
                tenant_id=tenant_id,
                inquiry_id=inquiry_id,
                conversation_id=assessment.conversation_id,
                human_task_id=task.id,
                triggers=[trigger.value for trigger in assessment.codes],
                summary=assessment.summary,
                summary_version=SUMMARY_VERSION,
                created_by=requested_by,
            )
            db.add(created_record)
            db.flush()
            self._audit.record(
                db,
                action="sales.handoff_created",
                actor=actor,
                subject_type="inquiry",
                subject_id=inquiry_id,
                after={
                    "task_id": task.id,
                    "handoff_id": created_record.id,
                    "triggers": list(created_record.triggers),
                    "priority": task.priority,
                    "assignee_role": task.assignee_role,
                },
            )
            return HandoffResult(
                assessment=assessment,
                task=task,
                record=created_record,
                created=True,
                refreshed=False,
            )

        existing_record = db.execute(
            select(ConversationHandoff).where(
                ConversationHandoff.tenant_id == tenant_id,
                ConversationHandoff.human_task_id == task.id,
            )
        ).scalar_one_or_none()
        record = existing_record
        existing = tuple(record.triggers) if record is not None else ()
        desired = _merged_triggers(existing, assessment.codes)
        if record is not None and tuple(record.triggers) == tuple(
            trigger.value for trigger in desired
        ):
            return HandoffResult(
                assessment=assessment,
                task=task,
                record=record,
                created=False,
                refreshed=False,
            )

        added = tuple(trigger for trigger in desired if trigger.value not in set(existing))
        before = {
            "triggers": list(existing),
            "priority": task.priority,
            "assignee_role": task.assignee_role,
            "title": task.title,
        }
        if record is None:
            record = ConversationHandoff(
                tenant_id=tenant_id,
                inquiry_id=inquiry_id,
                conversation_id=assessment.conversation_id,
                human_task_id=task.id,
                triggers=[trigger.value for trigger in desired],
                summary=assessment.summary,
                summary_version=SUMMARY_VERSION,
                created_by=requested_by,
            )
            db.add(record)
        else:
            record.triggers = [trigger.value for trigger in desired]
            record.summary = (
                _append_trigger_note(record.summary, added) if added else assessment.summary
            )
            record.summary_version = SUMMARY_VERSION
            record.conversation_id = assessment.conversation_id

        task.title = _title(desired)
        task.priority = self._policy.priority(desired)
        task.assignee_role = self._policy.assignee_role(desired)
        db.flush()
        self._audit.record(
            db,
            action="sales.handoff_refreshed",
            actor=actor,
            subject_type="inquiry",
            subject_id=inquiry_id,
            before=before,
            after={
                "triggers": [trigger.value for trigger in desired],
                "added": [trigger.value for trigger in added],
                "priority": task.priority,
                "assignee_role": task.assignee_role,
                "title": task.title,
                "handoff_id": record.id,
            },
        )
        return HandoffResult(
            assessment=assessment,
            task=task,
            record=record,
            created=False,
            refreshed=True,
        )

    # -- internals --------------------------------------------------------

    def _inquiry(self, db: Session, inquiry_id: UUID, tenant_id: UUID) -> Inquiry:
        inquiry = db.execute(
            select(Inquiry).where(Inquiry.id == inquiry_id, Inquiry.tenant_id == tenant_id)
        ).scalar_one_or_none()
        if inquiry is None:
            raise SalesNotFoundError(f"inquiry {inquiry_id}")
        # A closed or lost inquiry can still need a human: complaints and
        # refunds usually arrive after the sale. Do not refuse it here.
        return inquiry

    def _conversation(self, db: Session, inquiry: Inquiry, tenant_id: UUID) -> Conversation | None:
        if inquiry.conversation_id is None:
            return None
        conversation = db.execute(
            select(Conversation).where(
                Conversation.id == inquiry.conversation_id,
                Conversation.tenant_id == tenant_id,
            )
        ).scalar_one_or_none()
        if conversation is None:
            raise SalesNotFoundError(f"conversation {inquiry.conversation_id}")
        return conversation

    def _facts(
        self,
        db: Session,
        inquiry: Inquiry,
        conversation: Conversation | None,
        tenant_id: UUID,
        now: datetime,
    ) -> tuple[ConversationFacts, tuple[str, ...], tuple[tuple[datetime, str], ...]]:
        customer = None
        if inquiry.customer_id is not None:
            customer = db.execute(
                select(Customer).where(
                    Customer.id == inquiry.customer_id,
                    Customer.tenant_id == tenant_id,
                )
            ).scalar_one_or_none()

        contact = None
        if conversation is not None and conversation.contact_id is not None:
            contact = db.execute(
                select(Contact).where(
                    Contact.id == conversation.contact_id,
                    Contact.tenant_id == tenant_id,
                )
            ).scalar_one_or_none()

        requirement: Requirement | None = None
        missing: tuple[str, ...] = ()
        row = db.execute(
            select(RequirementRow)
            .where(
                RequirementRow.tenant_id == tenant_id,
                RequirementRow.inquiry_id == inquiry.id,
            )
            .order_by(RequirementRow.version.desc())
            .limit(1)
        ).scalar_one_or_none()
        if row is not None:
            missing = _missing_labels(row)
            try:
                requirement = Requirement.model_validate(row.data)
            except ValidationError:
                # A corrupt requirement must not block a complaint handoff.
                missing = (*missing, "requirement data could not be read")

        pending_drafts = db.execute(
            select(func.count())
            .select_from(MessageDraft)
            .where(
                MessageDraft.tenant_id == tenant_id,
                MessageDraft.inquiry_id == inquiry.id,
                MessageDraft.status == "draft",
            )
        ).scalar_one()

        if conversation is None:
            facts = ConversationFacts(
                inquiry_state=inquiry.state,
                trip_type=inquiry.trip_type,
                urgency=inquiry.urgency,
                customer_name=customer.display_name if customer is not None else None,
                channel="unknown",
                language=None,
                requirement=requirement,
                stated_fields=requirement.asserted_fields() if requirement else (),
                missing_fields=missing,
                inbound_count=0,
                outbound_count=0,
                last_inbound_at=None,
                last_outbound_at=None,
                last_inbound=None,
                last_outbound=None,
                pending_drafts=pending_drafts,
                generated_at=now,
            )
            return facts, (), ()

        message_filter = (
            Message.tenant_id == tenant_id,
            Message.conversation_id == conversation.id,
        )
        inbound_count = db.execute(
            select(func.count())
            .select_from(Message)
            .where(*message_filter, Message.direction == "inbound")
        ).scalar_one()
        outbound_count = db.execute(
            select(func.count())
            .select_from(Message)
            .where(*message_filter, Message.direction == "outbound")
        ).scalar_one()
        recent_inbound = list(
            db.execute(
                select(Message)
                .where(*message_filter, Message.direction == "inbound")
                .order_by(Message.occurred_at.desc(), Message.created_at.desc())
                .limit(_MAX_RECENT_INBOUND)
            ).scalars()
        )
        latest_outbound = db.execute(
            select(Message)
            .where(*message_filter, Message.direction == "outbound")
            .order_by(Message.occurred_at.desc(), Message.created_at.desc())
            .limit(1)
        ).scalar_one_or_none()
        windowed = list(
            db.execute(
                select(Message)
                .where(
                    *message_filter,
                    Message.direction == "inbound",
                    Message.occurred_at >= now - self._policy.loop_window,
                )
                .order_by(Message.occurred_at.desc(), Message.created_at.desc())
                .limit(_MAX_LOOP_MESSAGES)
            ).scalars()
        )

        texts = tuple(
            dict.fromkeys(message.body for message in (*recent_inbound, *windowed) if message.body)
        )
        loop_messages = tuple((message.occurred_at, message.body) for message in windowed)
        facts = ConversationFacts(
            inquiry_state=inquiry.state,
            trip_type=inquiry.trip_type,
            urgency=inquiry.urgency,
            customer_name=customer.display_name if customer is not None else None,
            channel=conversation.channel,
            language=contact.language if contact is not None else None,
            requirement=requirement,
            stated_fields=requirement.asserted_fields() if requirement else (),
            missing_fields=missing,
            inbound_count=inbound_count,
            outbound_count=outbound_count,
            last_inbound_at=recent_inbound[0].occurred_at if recent_inbound else None,
            last_outbound_at=latest_outbound.occurred_at if latest_outbound else None,
            last_inbound=recent_inbound[0].body if recent_inbound else None,
            last_outbound=latest_outbound.body if latest_outbound else None,
            pending_drafts=pending_drafts,
            generated_at=now,
        )
        return facts, texts, loop_messages

    def _open_task(self, db: Session, inquiry_id: UUID, tenant_id: UUID) -> HumanTask | None:
        return db.execute(
            select(HumanTask)
            .where(
                HumanTask.tenant_id == tenant_id,
                HumanTask.task_type == _TASK_TYPE,
                HumanTask.subject_type == "inquiry",
                HumanTask.subject_id == inquiry_id,
                HumanTask.status.in_(_OPEN_STATES),
            )
            .order_by(HumanTask.created_at.desc(), HumanTask.id.desc())
            .limit(1)
        ).scalar_one_or_none()
