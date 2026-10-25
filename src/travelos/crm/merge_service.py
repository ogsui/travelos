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
from travelos.crm.errors import CRMValidationError
from travelos.crm.merge_preview import load_merge_snapshot, merge_proposal
from travelos.crm.models import CustomerMergeHistory


@dataclass(frozen=True, slots=True)
class CustomerMergeApplyResult:
    approval_id: UUID
    source_customer_id: UUID
    target_customer_id: UUID
    moved_contacts: int
    moved_travellers: int
    already_applied: bool


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


def _uuid(value: Any) -> UUID | None:
    try:
        return UUID(str(value))
    except (TypeError, ValueError):
        return None
