"""Privacy-conscious, approval-backed customer merge previews."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.approval.service import ApprovalService, Submission
from travelos.core.audit import Actor
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.crm.errors import CRMRecordNotFoundError, CRMValidationError
from travelos.crm.fingerprints import ContactFingerprinter
from travelos.crm.models import Consent, Contact, Customer, Traveller


@dataclass(frozen=True, slots=True)
class CustomerMergePreview:
    source: Customer
    target: Customer
    source_contacts: int
    target_contacts: int
    source_travellers: int
    target_travellers: int
    source_consents: int
    overlapping_contacts: tuple[tuple[str, int], ...]
    submission: Submission


@dataclass(frozen=True, slots=True)
class CustomerMergeSnapshot:
    source: Customer
    target: Customer
    source_contacts: tuple[Contact, ...]
    target_contacts: tuple[Contact, ...]
    source_travellers: tuple[Traveller, ...]
    target_travellers: tuple[Traveller, ...]
    source_consents: tuple[Consent, ...]
    overlap_counts: tuple[tuple[str, int], ...]
    digest: str


class CustomerMergePreviewService:
    """Preview the affected records and request approval for that exact snapshot."""

    def __init__(self, fingerprinter: ContactFingerprinter | None = None) -> None:
        self._fingerprinter = fingerprinter or ContactFingerprinter()

    def preview(
        self,
        db: Session,
        *,
        source_customer_id: UUID,
        target_customer_id: UUID,
        approvals: ApprovalService,
        actor: Actor,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        ip: str | None = None,
    ) -> CustomerMergePreview:
        snapshot = load_merge_snapshot(
            db,
            source_customer_id=source_customer_id,
            target_customer_id=target_customer_id,
            tenant_id=tenant_id,
            fingerprinter=self._fingerprinter,
        )
        submission = approvals.submit(
            db,
            action_type="crm.customer_merge",
            subject_type="crm_customer_merge",
            subject_id=source_customer_id,
            proposed=merge_proposal(snapshot),
            requested_by=actor.id,
            reason="Customer merge requires human review",
            context={"overlapping_contacts": dict(snapshot.overlap_counts)},
            tenant_id=tenant_id,
            ip=ip,
        )
        return CustomerMergePreview(
            source=snapshot.source,
            target=snapshot.target,
            source_contacts=len(snapshot.source_contacts),
            target_contacts=len(snapshot.target_contacts),
            source_travellers=len(snapshot.source_travellers),
            target_travellers=len(snapshot.target_travellers),
            source_consents=len(snapshot.source_consents),
            overlapping_contacts=snapshot.overlap_counts,
            submission=submission,
        )


def load_merge_snapshot(
    db: Session,
    *,
    source_customer_id: UUID,
    target_customer_id: UUID,
    tenant_id: UUID,
    fingerprinter: ContactFingerprinter | None = None,
    lock: bool = False,
) -> CustomerMergeSnapshot:
    if source_customer_id == target_customer_id:
        raise CRMValidationError("source and target customers must be different")
    statement = select(Customer).where(
        Customer.id.in_([source_customer_id, target_customer_id]),
        Customer.tenant_id == tenant_id,
    )
    if lock:
        statement = statement.order_by(Customer.id).with_for_update()
    by_id = {customer.id: customer for customer in db.execute(statement).scalars().all()}
    source = by_id.get(source_customer_id)
    target = by_id.get(target_customer_id)
    if source is None or target is None:
        raise CRMRecordNotFoundError("customer not found")

    customer_ids = [source.id, target.id]
    contact_statement = (
        select(Contact)
        .where(Contact.tenant_id == tenant_id, Contact.customer_id.in_(customer_ids))
        .order_by(Contact.id)
    )
    traveller_statement = (
        select(Traveller)
        .where(Traveller.tenant_id == tenant_id, Traveller.customer_id.in_(customer_ids))
        .order_by(Traveller.id)
    )
    if lock:
        contact_statement = contact_statement.with_for_update()
        traveller_statement = traveller_statement.with_for_update()
    contacts = db.execute(contact_statement).scalars().all()
    travellers = db.execute(traveller_statement).scalars().all()
    contact_ids = [contact.id for contact in contacts]
    consents = (
        db.execute(
            select(Consent)
            .where(Consent.tenant_id == tenant_id, Consent.contact_id.in_(contact_ids))
            .order_by(Consent.id)
        )
        .scalars()
        .all()
        if contact_ids
        else []
    )

    source_contacts = tuple(contact for contact in contacts if contact.customer_id == source.id)
    target_contacts = tuple(contact for contact in contacts if contact.customer_id == target.id)
    source_travellers = tuple(row for row in travellers if row.customer_id == source.id)
    target_travellers = tuple(row for row in travellers if row.customer_id == target.id)
    source_contact_ids = {contact.id for contact in source_contacts}
    source_consents = tuple(
        consent for consent in consents if consent.contact_id in source_contact_ids
    )
    source_keys = Counter(
        (contact.channel, contact.value_hash)
        for contact in source_contacts
        if contact.value_hash is not None
    )
    target_keys = Counter(
        (contact.channel, contact.value_hash)
        for contact in target_contacts
        if contact.value_hash is not None
    )
    overlap_by_channel: Counter[str] = Counter()
    for channel, digest in source_keys.keys() & target_keys.keys():
        overlap_by_channel[channel] += min(
            source_keys[(channel, digest)], target_keys[(channel, digest)]
        )
    overlap_counts = tuple(sorted(overlap_by_channel.items()))

    digest_payload = {
        "source": _customer_state(source),
        "target": _customer_state(target),
        "contacts": [
            [str(row.id), str(row.customer_id), row.channel, row.value_hash, row.is_primary]
            for row in contacts
        ],
        "travellers": [[str(row.id), str(row.customer_id)] for row in travellers],
        "consents": [
            [
                str(row.id),
                str(row.contact_id),
                row.channel,
                row.purpose,
                row.status,
                row.granted_at.isoformat(),
                row.withdrawn_at.isoformat() if row.withdrawn_at else None,
            ]
            for row in consents
        ],
    }
    encoded = json.dumps(digest_payload, sort_keys=True, separators=(",", ":")).encode()
    digest = (fingerprinter or ContactFingerprinter()).keyed_digest(
        "crm-customer-merge-v1", encoded
    )
    return CustomerMergeSnapshot(
        source=source,
        target=target,
        source_contacts=source_contacts,
        target_contacts=target_contacts,
        source_travellers=source_travellers,
        target_travellers=target_travellers,
        source_consents=source_consents,
        overlap_counts=overlap_counts,
        digest=digest,
    )


def merge_proposal(snapshot: CustomerMergeSnapshot) -> dict[str, Any]:
    return {
        "source_customer_id": str(snapshot.source.id),
        "target_customer_id": str(snapshot.target.id),
        "source_contacts": len(snapshot.source_contacts),
        "target_contacts": len(snapshot.target_contacts),
        "source_travellers": len(snapshot.source_travellers),
        "target_travellers": len(snapshot.target_travellers),
        "source_consents": len(snapshot.source_consents),
        "overlapping_contacts": dict(snapshot.overlap_counts),
        "snapshot_hash": snapshot.digest,
    }


def _customer_state(customer: Customer) -> dict[str, object]:
    return {
        "id": str(customer.id),
        "name": customer.display_name,
        "type": customer.customer_type,
        "segment": customer.segment,
        "merged_into": str(getattr(customer, "merged_into_customer_id", None) or ""),
    }
