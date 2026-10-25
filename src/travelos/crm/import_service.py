"""Approval-gated and idempotent writes for CRM import batches."""

from __future__ import annotations

from dataclasses import dataclass, replace
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from travelos.approval.models import ApprovalRequest
from travelos.approval.service import ApprovalService, Submission
from travelos.core.audit import Actor, AuditWriter
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.models import IdempotencyKey
from travelos.crm.consent import ConsentService
from travelos.crm.errors import CRMValidationError
from travelos.crm.importer import (
    CRMImportMapping,
    CRMImportReport,
    DuplicateCandidate,
    parse_customer_import,
)
from travelos.crm.models import Contact
from travelos.crm.service import CRMService


@dataclass(frozen=True, slots=True)
class CRMImportResult:
    imported_customers: int
    imported_contacts: int
    granted_consents: int
    duplicate_rows_skipped: int
    already_applied: bool


class CRMImportService:
    """Build dry-run reports, request approval and apply only the approved batch."""

    def __init__(self, audit: AuditWriter | None = None) -> None:
        self._audit = audit or AuditWriter()

    def preview(
        self,
        db: Session,
        *,
        content: bytes,
        filename: str,
        mapping: CRMImportMapping,
        tenant_id: UUID = DEFAULT_TENANT_ID,
    ) -> CRMImportReport:
        report = parse_customer_import(content, filename=filename, mapping=mapping)
        known: dict[tuple[str, str], UUID] = {}
        fingerprints = {
            (contact.channel, contact.fingerprint)
            for row in report.valid_rows
            for contact in row.contacts
        }
        for channel, fingerprint in fingerprints:
            existing = db.execute(
                select(Contact.customer_id)
                .where(
                    Contact.tenant_id == tenant_id,
                    Contact.channel == channel,
                    Contact.value_hash == fingerprint,
                )
                .order_by(Contact.created_at, Contact.id)
                .limit(1)
            ).scalar_one_or_none()
            if existing is not None:
                known[(channel, fingerprint)] = existing

        existing_candidates = [
            candidate
            for row in report.valid_rows
            for contact in row.contacts
            if (existing_id := known.get((contact.channel, contact.fingerprint))) is not None
            for candidate in [
                _existing_duplicate(
                    contact.channel, contact.fingerprint, row.row_number, existing_id
                )
            ]
        ]
        return replace(
            report,
            duplicate_candidates=report.duplicate_candidates + tuple(existing_candidates),
        )

    def request_approval(
        self,
        db: Session,
        *,
        report: CRMImportReport,
        approvals: ApprovalService,
        actor: Actor,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        ip: str | None = None,
    ) -> Submission:
        if not report.valid_rows:
            raise CRMValidationError("import has no valid customer rows")
        return approvals.submit(
            db,
            action_type="crm.bulk_import",
            subject_type="crm_import_batch",
            proposed={
                "batch_key": report.batch_key,
                "source_hash": report.source_hash,
                "mapping_hash": report.mapping_hash,
                "rows_seen": report.rows_seen,
                "valid_rows": len(report.valid_rows),
                "bad_rows": len(report.bad_rows),
                "within_batch_duplicates": report.within_batch_duplicate_count,
            },
            requested_by=actor.id,
            reason="CRM import batch requires human approval",
            context={"duplicate_candidates": len(report.duplicate_candidates)},
            tenant_id=tenant_id,
            ip=ip,
        )

    def apply_approved(
        self,
        db: Session,
        *,
        content: bytes,
        filename: str,
        mapping: CRMImportMapping,
        approval_id: UUID,
        actor: Actor,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        ip: str | None = None,
    ) -> CRMImportResult:
        report = self.preview(
            db,
            content=content,
            filename=filename,
            mapping=mapping,
            tenant_id=tenant_id,
        )
        approval = db.execute(
            select(ApprovalRequest).where(
                ApprovalRequest.id == approval_id,
                ApprovalRequest.tenant_id == tenant_id,
            )
        ).scalar_one_or_none()
        if not _approval_matches(approval, report):
            raise CRMValidationError("an approved request for this exact import batch is required")

        scope = f"crm.import:{tenant_id}"
        claim = (
            pg_insert(IdempotencyKey)
            .values(
                key=report.batch_key,
                scope=scope,
                response_hash=report.batch_key,
                expires_at=None,
                tenant_id=tenant_id,
            )
            .on_conflict_do_nothing(index_elements=["key", "scope"])
            .returning(IdempotencyKey.id)
        )
        claimed = db.scalar(claim)
        if claimed is None:
            return CRMImportResult(0, 0, 0, len(report.valid_rows), True)

        crm = CRMService(self._audit)
        consents = ConsentService(self._audit)
        customer_count = 0
        contact_count = 0
        consent_count = 0
        skipped_rows = len(report.valid_rows) - len(report.importable_rows)
        for row in report.importable_rows:
            customer = crm.create_customer(
                db,
                customer_type=row.customer_type,
                display_name=row.display_name,
                segment=row.segment,
                actor=actor,
                tenant_id=tenant_id,
            )
            customer_count += 1
            for index, contact in enumerate(row.contacts):
                saved_contact = crm.create_contact(
                    db,
                    customer_id=customer.id,
                    channel=contact.channel,
                    value=contact.value,
                    verified=False,
                    is_primary=index == 0,
                    language=None,
                    actor=actor,
                    tenant_id=tenant_id,
                )
                contact_count += 1
                if contact.marketing_consent and contact.consent_proof_ref:
                    consents.grant(
                        db,
                        contact_id=saved_contact.id,
                        channel=saved_contact.channel,
                        purpose="marketing",
                        source="legacy_import",
                        proof_ref=contact.consent_proof_ref,
                        actor=actor,
                        tenant_id=tenant_id,
                    )
                    consent_count += 1

        self._audit.record(
            db,
            action="crm.import_applied",
            actor=actor,
            subject_type="crm_import_batch",
            subject_id=approval_id,
            after={
                "batch_key": report.batch_key,
                "approval_id": approval_id,
                "customers": customer_count,
                "contacts": contact_count,
                "consents": consent_count,
                "duplicates_skipped": skipped_rows,
            },
            ip=ip,
        )
        return CRMImportResult(customer_count, contact_count, consent_count, skipped_rows, False)


def _approval_matches(approval: ApprovalRequest | None, report: CRMImportReport) -> bool:
    if approval is None or approval.action_type != "crm.bulk_import":
        return False
    if approval.status != "approved" or approval.final != approval.proposed:
        return False
    return (
        approval.proposed.get("batch_key") == report.batch_key
        and approval.proposed.get("source_hash") == report.source_hash
        and approval.proposed.get("mapping_hash") == report.mapping_hash
    )


def _existing_duplicate(
    channel: str,
    fingerprint: str,
    row_number: int,
    customer_id: UUID,
) -> DuplicateCandidate:
    return DuplicateCandidate(
        channel=channel,
        fingerprint=fingerprint,
        first_row=None,
        duplicate_row=row_number,
        existing_customer_id=customer_id,
    )
