"""Transactional application of approved CRM customer merges."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.approval.models import ApprovalRequest
from travelos.core.audit import Actor, AuditWriter
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.crm.errors import CRMRecordNotFoundError, CRMValidationError
from travelos.crm.merge_preview import load_merge_snapshot, merge_proposal
from travelos.crm.models import Contact, Customer, CustomerMergeHistory, Traveller


@dataclass(frozen=True, slots=True)
class CustomerMergeApplyResult:
    approval_id: UUID
    source_customer_id: UUID
    target_customer_id: UUID
    moved_contacts: int
    moved_travellers: int
    already_applied: bool


@dataclass(frozen=True, slots=True)
class CustomerMergeUndoResult:
    approval_id: UUID
    restored_contacts: int
    restored_travellers: int
    already_undone: bool


class CustomerMergeService:
    def __init__(self, audit: AuditWriter | None = None) -> None:
        self._audit = audit or AuditWriter()

    def apply_approved(
        self,
        db: Session,
        *,
        approval_id: UUID,
        actor: Actor,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        ip: str | None = None,
    ) -> CustomerMergeApplyResult:
        approval = db.execute(
            select(ApprovalRequest)
            .where(ApprovalRequest.id == approval_id, ApprovalRequest.tenant_id == tenant_id)
            .with_for_update()
        ).scalar_one_or_none()
        history = db.execute(
            select(CustomerMergeHistory)
            .where(
                CustomerMergeHistory.approval_id == approval_id,
                CustomerMergeHistory.tenant_id == tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if history is not None:
            if history.state == "undone":
                raise CRMValidationError("an undone customer merge cannot be replayed")
            return CustomerMergeApplyResult(
                approval_id=approval_id,
                source_customer_id=history.source_customer_id,
                target_customer_id=history.target_customer_id,
                moved_contacts=len(history.contacts_before),
                moved_travellers=len(history.traveller_ids),
                already_applied=True,
            )
        if (
            approval is None
            or approval.action_type != "crm.customer_merge"
            or approval.subject_type != "crm_customer_merge"
            or approval.status != "approved"
            or approval.final != approval.proposed
        ):
            raise CRMValidationError("an approved request for this exact merge is required")

        source_id = _uuid(approval.proposed.get("source_customer_id"))
        target_id = _uuid(approval.proposed.get("target_customer_id"))
        if source_id is None or target_id is None or approval.subject_id != source_id:
            raise CRMValidationError("approved merge proposal is invalid")
        snapshot = load_merge_snapshot(
            db,
            source_customer_id=source_id,
            target_customer_id=target_id,
            tenant_id=tenant_id,
            lock=True,
        )
        if merge_proposal(snapshot) != approval.proposed:
            raise CRMValidationError("customer merge snapshot changed; create a new preview")

        target_has_primary = any(row.is_primary for row in snapshot.target_contacts)
        contacts_before = [
            {
                "id": str(row.id),
                "customer_id": str(row.customer_id),
                "is_primary": row.is_primary,
            }
            for row in (*snapshot.source_contacts, *snapshot.target_contacts)
        ]
        # Clear source primary first so the partial unique index cannot reject
        # the row transfer when the target already has a primary contact.
        source_primary = next((row for row in snapshot.source_contacts if row.is_primary), None)
        if source_primary is not None and target_has_primary:
            source_primary.is_primary = False
            db.flush()
        for contact in snapshot.source_contacts:
            contact.customer_id = target_id
        for traveller in snapshot.source_travellers:
            traveller.customer_id = target_id
        db.flush()

        if source_primary is not None and not target_has_primary:
            source_primary.is_primary = True
        source = snapshot.source
        source.merged_into_customer_id = target_id
        source.merged_at = datetime.now(UTC)
        contacts_after = [
            {
                "id": str(row.id),
                "customer_id": str(row.customer_id),
                "is_primary": row.is_primary,
            }
            for row in (*snapshot.source_contacts, *snapshot.target_contacts)
        ]
        history = CustomerMergeHistory(
            tenant_id=tenant_id,
            approval_id=approval_id,
            source_customer_id=source_id,
            target_customer_id=target_id,
            contacts_before=contacts_before,
            contacts_after=contacts_after,
            traveller_ids=[str(row.id) for row in snapshot.source_travellers],
            state="applied",
        )
        db.add(history)
        db.flush()
        self._audit.record(
            db,
            action="crm.customer_merge_applied",
            actor=actor,
            subject_type="crm_customer_merge",
            subject_id=approval_id,
            after={
                "source_customer_id": source_id,
                "target_customer_id": target_id,
                "contacts": len(snapshot.source_contacts),
                "travellers": len(snapshot.source_travellers),
                "history_id": history.id,
            },
            ip=ip,
        )
        return CustomerMergeApplyResult(
            approval_id=approval_id,
            source_customer_id=source_id,
            target_customer_id=target_id,
            moved_contacts=len(snapshot.source_contacts),
            moved_travellers=len(snapshot.source_travellers),
            already_applied=False,
        )

    def undo(
        self,
        db: Session,
        *,
        approval_id: UUID,
        actor: Actor,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        ip: str | None = None,
    ) -> CustomerMergeUndoResult:
        history = db.execute(
            select(CustomerMergeHistory)
            .where(
                CustomerMergeHistory.approval_id == approval_id,
                CustomerMergeHistory.tenant_id == tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if history is None:
            raise CRMRecordNotFoundError("customer merge not found")
        if history.state == "undone":
            return CustomerMergeUndoResult(approval_id, 0, 0, True)

        customers = (
            db.execute(
                select(Customer)
                .where(
                    Customer.tenant_id == tenant_id,
                    Customer.id.in_([history.source_customer_id, history.target_customer_id]),
                )
                .order_by(Customer.id)
                .with_for_update()
            )
            .scalars()
            .all()
        )
        customers_by_id = {row.id: row for row in customers}
        source = customers_by_id.get(history.source_customer_id)
        target = customers_by_id.get(history.target_customer_id)
        if source is None or target is None or source.merged_into_customer_id != target.id:
            raise CRMValidationError("customer merge state changed; undo is unsafe")

        before = _index_contact_history(history.contacts_before)
        after = _index_contact_history(history.contacts_after)
        contact_ids = [UUID(value) for value in after]
        contacts = (
            db.execute(
                select(Contact)
                .where(Contact.tenant_id == tenant_id, Contact.id.in_(contact_ids))
                .order_by(Contact.id)
                .with_for_update()
            )
            .scalars()
            .all()
        )
        if len(contacts) != len(contact_ids):
            raise CRMValidationError("merge contacts changed; undo is unsafe")
        contacts_by_id = {row.id: row for row in contacts}
        for contact_id, expected in after.items():
            contact = contacts_by_id[UUID(contact_id)]
            if str(contact.customer_id) != str(
                expected.get("customer_id")
            ) or contact.is_primary != bool(expected.get("is_primary")):
                raise CRMValidationError("merge contacts changed; undo is unsafe")

        current_primary = (
            db.execute(
                select(Contact)
                .where(
                    Contact.tenant_id == tenant_id,
                    Contact.customer_id == target.id,
                    Contact.is_primary.is_(True),
                )
                .with_for_update()
            )
            .scalars()
            .all()
        )
        if any(row.id not in contacts_by_id for row in current_primary):
            raise CRMValidationError("target primary contact changed; undo is unsafe")

        traveller_ids = [UUID(value) for value in history.traveller_ids]
        travellers = (
            db.execute(
                select(Traveller)
                .where(Traveller.tenant_id == tenant_id, Traveller.id.in_(traveller_ids))
                .order_by(Traveller.id)
                .with_for_update()
            )
            .scalars()
            .all()
            if traveller_ids
            else []
        )
        if len(travellers) != len(traveller_ids) or any(
            row.customer_id != target.id for row in travellers
        ):
            raise CRMValidationError("merge travellers changed; undo is unsafe")

        restored_contacts = sum(
            str(row.get("customer_id")) == str(source.id) for row in before.values()
        )
        for contact in contacts:
            contact.is_primary = False
        db.flush()
        for contact_id, original in before.items():
            contact = contacts_by_id[UUID(contact_id)]
            contact.customer_id = UUID(str(original.get("customer_id")))
            contact.is_primary = bool(original.get("is_primary"))
        for traveller in travellers:
            traveller.customer_id = source.id
        source.merged_into_customer_id = None
        source.merged_at = None
        history.state = "undone"
        history.undone_at = datetime.now(UTC)
        db.flush()
        self._audit.record(
            db,
            action="crm.customer_merge_undone",
            actor=actor,
            subject_type="crm_customer_merge",
            subject_id=approval_id,
            after={
                "source_customer_id": source.id,
                "target_customer_id": target.id,
                "contacts": restored_contacts,
                "travellers": len(travellers),
            },
            ip=ip,
        )
        return CustomerMergeUndoResult(approval_id, restored_contacts, len(travellers), False)


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None


def _index_contact_history(
    rows: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    indexed: dict[str, dict[str, object]] = {}
    for row in rows:
        contact_id = _uuid(row.get("id"))
        if contact_id is None or str(contact_id) in indexed:
            raise CRMValidationError("merge history is invalid")
        indexed[str(contact_id)] = row
    return indexed
