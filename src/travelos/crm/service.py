"""Tenant-scoped customer, contact and traveller operations."""

from __future__ import annotations

from datetime import UTC, date, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.core.audit import Actor, AuditWriter
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.crm.errors import CRMRecordNotFoundError, CRMValidationError
from travelos.crm.fingerprints import ContactFingerprinter
from travelos.crm.models import Contact, Customer, Traveller

_CUSTOMER_TYPES = frozenset({"individual", "family", "corporate"})
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


class CRMService:
    """CRUD operations with tenant checks and privacy-conscious audit rows."""

    def __init__(self, audit: AuditWriter | None = None) -> None:
        self._audit = audit or AuditWriter()

    def create_customer(
        self,
        db: Session,
        *,
        customer_type: str,
        display_name: str,
        segment: str | None,
        actor: Actor,
        tenant_id: UUID = DEFAULT_TENANT_ID,
    ) -> Customer:
        normalized_name = display_name.strip()
        if customer_type not in _CUSTOMER_TYPES:
            raise CRMValidationError("unsupported customer type")
        if not normalized_name or len(normalized_name) > 200:
            raise CRMValidationError("display name must contain 1 to 200 characters")
        customer = Customer(
            tenant_id=tenant_id,
            customer_type=customer_type,
            display_name=normalized_name,
            segment=segment.strip() if segment and segment.strip() else None,
        )
        db.add(customer)
        db.flush()
        self._audit.record(
            db,
            action="crm.customer_created",
            actor=actor,
            subject_type="customer",
            subject_id=customer.id,
            after={
                "customer_type": customer.customer_type,
                "has_segment": customer.segment is not None,
            },
        )
        return customer

    def get_customer(
        self, db: Session, *, customer_id: UUID, tenant_id: UUID = DEFAULT_TENANT_ID
    ) -> Customer:
        customer = db.execute(
            select(Customer).where(Customer.id == customer_id, Customer.tenant_id == tenant_id)
        ).scalar_one_or_none()
        if customer is None:
            raise CRMRecordNotFoundError("customer not found")
        return customer

    def list_customers(
        self,
        db: Session,
        *,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        limit: int = 50,
        offset: int = 0,
    ) -> list[Customer]:
        return list(
            db.execute(
                select(Customer)
                .where(
                    Customer.tenant_id == tenant_id,
                    Customer.merged_into_customer_id.is_(None),
                )
                .order_by(Customer.created_at.desc(), Customer.id.desc())
                .limit(limit)
                .offset(offset)
            )
            .scalars()
            .all()
        )

    def create_contact(
        self,
        db: Session,
        *,
        customer_id: UUID,
        channel: str,
        value: str,
        verified: bool,
        is_primary: bool,
        language: str | None,
        actor: Actor,
        tenant_id: UUID = DEFAULT_TENANT_ID,
    ) -> Contact:
        self._get_active_customer(db, customer_id=customer_id, tenant_id=tenant_id)
        normalized_value = value.strip()
        if channel not in _CHANNELS:
            raise CRMValidationError("unsupported contact channel")
        if not normalized_value or len(normalized_value) > 320:
            raise CRMValidationError("contact value must contain 1 to 320 characters")
        if language is not None and len(language) > 10:
            raise CRMValidationError("language tag is too long")
        try:
            value_hash = ContactFingerprinter().fingerprint(channel, normalized_value)
        except ValueError as exc:
            raise CRMValidationError(str(exc)) from exc

        if is_primary:
            self._clear_primary_contact(
                db, customer_id=customer_id, actor=actor, tenant_id=tenant_id
            )
        contact = Contact(
            tenant_id=tenant_id,
            customer_id=customer_id,
            channel=channel,
            value=normalized_value,
            value_hash=value_hash,
            verified=verified,
            is_primary=is_primary,
            language=language.strip().lower() if language and language.strip() else None,
        )
        db.add(contact)
        db.flush()
        self._audit.record(
            db,
            action="crm.contact_created",
            actor=actor,
            subject_type="contact",
            subject_id=contact.id,
            after={
                "customer_id": customer_id,
                "channel": channel,
                "verified": verified,
                "is_primary": is_primary,
            },
        )
        return contact

    def list_contacts(
        self, db: Session, *, customer_id: UUID, tenant_id: UUID = DEFAULT_TENANT_ID
    ) -> list[Contact]:
        self.get_customer(db, customer_id=customer_id, tenant_id=tenant_id)
        return list(
            db.execute(
                select(Contact)
                .where(Contact.customer_id == customer_id, Contact.tenant_id == tenant_id)
                .order_by(Contact.is_primary.desc(), Contact.created_at, Contact.id)
            )
            .scalars()
            .all()
        )

    def create_traveller(
        self,
        db: Session,
        *,
        customer_id: UUID,
        full_name_as_passport: str | None,
        dob: date | None,
        gender: str | None,
        passport_no: str | None,
        passport_expiry: date | None,
        nationality: str | None,
        is_minor: bool,
        meal_pref: str | None,
        seat_pref: str | None,
        actor: Actor,
        tenant_id: UUID = DEFAULT_TENANT_ID,
    ) -> Traveller:
        self._get_active_customer(db, customer_id=customer_id, tenant_id=tenant_id)
        normalized_nationality = nationality.strip().upper() if nationality else None
        if normalized_nationality and len(normalized_nationality) != 2:
            raise CRMValidationError("nationality must be a two-letter code")
        if dob is not None and dob > datetime.now(UTC).date():
            raise CRMValidationError("date of birth cannot be in the future")

        traveller = Traveller(
            tenant_id=tenant_id,
            customer_id=customer_id,
            full_name_as_passport=_strip_optional(full_name_as_passport),
            dob=dob,
            gender=_strip_optional(gender),
            passport_no=_strip_optional(passport_no),
            passport_expiry=passport_expiry,
            nationality=normalized_nationality,
            is_minor=is_minor,
            meal_pref=_strip_optional(meal_pref),
            seat_pref=_strip_optional(seat_pref),
        )
        db.add(traveller)
        db.flush()
        self._audit.record(
            db,
            action="crm.traveller_created",
            actor=actor,
            subject_type="traveller",
            subject_id=traveller.id,
            after={"customer_id": customer_id, "is_minor": is_minor},
        )
        return traveller

    def list_travellers(
        self, db: Session, *, customer_id: UUID, tenant_id: UUID = DEFAULT_TENANT_ID
    ) -> list[Traveller]:
        self.get_customer(db, customer_id=customer_id, tenant_id=tenant_id)
        return list(
            db.execute(
                select(Traveller)
                .where(Traveller.customer_id == customer_id, Traveller.tenant_id == tenant_id)
                .order_by(Traveller.created_at, Traveller.id)
            )
            .scalars()
            .all()
        )

    def _clear_primary_contact(
        self,
        db: Session,
        *,
        customer_id: UUID,
        actor: Actor,
        tenant_id: UUID,
    ) -> None:
        contacts = db.execute(
            select(Contact).where(
                Contact.customer_id == customer_id,
                Contact.tenant_id == tenant_id,
                Contact.is_primary.is_(True),
            )
        ).scalars()
        for contact in contacts:
            contact.is_primary = False
            self._audit.record(
                db,
                action="crm.contact_primary_cleared",
                actor=actor,
                subject_type="contact",
                subject_id=contact.id,
                after={"customer_id": customer_id, "is_primary": False},
            )

    def _get_active_customer(self, db: Session, *, customer_id: UUID, tenant_id: UUID) -> Customer:
        customer = db.execute(
            select(Customer)
            .where(Customer.id == customer_id, Customer.tenant_id == tenant_id)
            .with_for_update()
        ).scalar_one_or_none()
        if customer is None:
            raise CRMRecordNotFoundError("customer not found")
        if customer.merged_into_customer_id is not None:
            raise CRMValidationError("cannot add records to a merged customer")
        return customer


def _strip_optional(value: str | None) -> str | None:
    stripped = value.strip() if value is not None else ""
    return stripped or None
