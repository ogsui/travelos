"""Consent decisions for outbound communication."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.core.audit import Actor, AuditWriter
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.crm.errors import CRMRecordNotFoundError, CRMValidationError
from travelos.crm.models import Consent, Contact, SuppressionEntry

_CHANNELS = frozenset(
    {
        "whatsapp",
        "email",
        "sms",
        "phone",
        "instagram",
        "facebook",
        "google_business",
        "web_form",
    }
)
_PURPOSES = frozenset({"marketing", "transactional", "reminders", "reviews"})


class ConsentService:
    """Grant, withdraw and check contact-level consent.

    Missing, mismatched, withdrawn and globally suppressed records always deny
    delivery. The caller owns the transaction and must commit explicitly.
    """

    def __init__(self, audit: AuditWriter | None = None) -> None:
        self._audit = audit or AuditWriter()

    def grant(
        self,
        db: Session,
        *,
        contact_id: UUID,
        channel: str,
        purpose: str,
        source: str,
        proof_ref: str | None,
        actor: Actor,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        now: datetime | None = None,
    ) -> Consent:
        contact = self._contact(db, contact_id=contact_id, tenant_id=tenant_id)
        self._validate_scope(contact, channel=channel, purpose=purpose)
        normalized_source = source.strip()
        if not normalized_source:
            raise CRMValidationError("consent source is required")
        granted_at = now or datetime.now(UTC)
        consent = Consent(
            tenant_id=tenant_id,
            contact_id=contact.id,
            channel=channel,
            purpose=purpose,
            status="granted",
            source=normalized_source,
            proof_ref=proof_ref,
            granted_at=granted_at,
        )
        db.add(consent)
        db.flush()
        self._audit.record(
            db,
            action="consent.granted",
            actor=actor,
            subject_type="consent",
            subject_id=consent.id,
            after={
                "contact_id": contact.id,
                "channel": channel,
                "purpose": purpose,
                "status": "granted",
                "source": normalized_source,
            },
        )
        return consent

    def withdraw(
        self,
        db: Session,
        *,
        consent_id: UUID,
        actor: Actor,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        now: datetime | None = None,
    ) -> Consent:
        consent = db.execute(
            select(Consent).where(
                Consent.id == consent_id,
                Consent.tenant_id == tenant_id,
            )
        ).scalar_one_or_none()
        if consent is None:
            raise CRMRecordNotFoundError("consent not found")
        if consent.status == "withdrawn":
            return consent

        before = {"status": consent.status, "withdrawn_at": consent.withdrawn_at}
        consent.status = "withdrawn"
        consent.withdrawn_at = now or datetime.now(UTC)
        db.flush()
        self._audit.record_change(
            db,
            action="consent.withdrawn",
            actor=actor,
            subject_type="consent",
            subject_id=consent.id,
            before=before,
            after={"status": consent.status, "withdrawn_at": consent.withdrawn_at},
        )
        return consent

    def can_send(
        self,
        db: Session,
        *,
        contact_id: UUID,
        channel: str,
        purpose: str,
        tenant_id: UUID = DEFAULT_TENANT_ID,
    ) -> bool:
        contact = self._contact(db, contact_id=contact_id, tenant_id=tenant_id)
        if channel not in _CHANNELS or purpose not in _PURPOSES or channel != contact.channel:
            return False
        if contact.value_hash is not None:
            suppressed = db.execute(
                select(SuppressionEntry.id).where(
                    SuppressionEntry.tenant_id == tenant_id,
                    SuppressionEntry.channel == channel,
                    SuppressionEntry.value_hash == contact.value_hash,
                )
            ).scalar_one_or_none()
            if suppressed is not None:
                return False

        latest = db.execute(
            select(Consent)
            .where(
                Consent.tenant_id == tenant_id,
                Consent.contact_id == contact.id,
                Consent.channel == channel,
                Consent.purpose == purpose,
            )
            .order_by(Consent.granted_at.desc(), Consent.created_at.desc(), Consent.id.desc())
            .limit(1)
        ).scalar_one_or_none()
        return latest is not None and latest.status == "granted" and latest.withdrawn_at is None

    @staticmethod
    def _contact(db: Session, *, contact_id: UUID, tenant_id: UUID) -> Contact:
        contact = db.execute(
            select(Contact).where(Contact.id == contact_id, Contact.tenant_id == tenant_id)
        ).scalar_one_or_none()
        if contact is None:
            raise CRMRecordNotFoundError("contact not found")
        return contact

    @staticmethod
    def _validate_scope(contact: Contact, *, channel: str, purpose: str) -> None:
        if channel not in _CHANNELS:
            raise CRMValidationError("unsupported consent channel")
        if purpose not in _PURPOSES:
            raise CRMValidationError("unsupported consent purpose")
        if channel != contact.channel:
            raise CRMValidationError("consent channel must match the contact channel")
