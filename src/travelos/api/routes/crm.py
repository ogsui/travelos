"""CRM consent API."""

from __future__ import annotations

from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from sqlalchemy.orm import Session

from travelos.api.deps import AuthContext, get_audit_writer, get_db, require
from travelos.core.audit import Actor, AuditWriter
from travelos.crm.consent import ConsentService
from travelos.crm.errors import CRMRecordNotFoundError, CRMValidationError
from travelos.crm.models import Consent, Contact, Customer, Traveller
from travelos.crm.service import CRMService
from travelos.security.permissions import Permission

router = APIRouter(prefix="/crm", tags=["crm"])


class ConsentGrantIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    contact_id: UUID
    channel: Annotated[str, Field(min_length=2, max_length=20)]
    purpose: Annotated[str, Field(min_length=2, max_length=20)]
    source: Annotated[str, Field(min_length=1, max_length=80)]
    proof_ref: Annotated[str | None, Field(default=None, max_length=255)]


class ConsentOut(BaseModel):
    id: UUID
    contact_id: UUID
    channel: str
    purpose: str
    status: str
    source: str
    proof_ref: str | None
    granted_at: datetime
    withdrawn_at: datetime | None

    @classmethod
    def from_model(cls, consent: Consent) -> ConsentOut:
        return cls.model_validate(consent, from_attributes=True)


class CanSendOut(BaseModel):
    can_send: bool


class CustomerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    customer_type: Literal["individual", "family", "corporate"]
    display_name: Annotated[str, Field(min_length=1, max_length=200)]
    segment: Annotated[str | None, Field(default=None, max_length=40)]


class CustomerOut(BaseModel):
    id: UUID
    customer_type: str
    display_name: str
    segment: str | None
    lifetime_value_paise: int
    lifetime_value_currency: str

    @classmethod
    def from_model(cls, customer: Customer) -> CustomerOut:
        return cls.model_validate(customer, from_attributes=True)


class ContactIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    channel: Literal[
        "whatsapp",
        "email",
        "sms",
        "phone",
        "instagram",
        "facebook",
        "google_business",
        "web_form",
    ]
    value: Annotated[str, Field(min_length=1, max_length=320)]
    verified: StrictBool = False
    is_primary: StrictBool = False
    language: Annotated[str | None, Field(default=None, max_length=10)]


class ContactOut(BaseModel):
    id: UUID
    customer_id: UUID
    channel: str
    value: str
    verified: bool
    is_primary: bool
    language: str | None

    @classmethod
    def from_model(cls, contact: Contact) -> ContactOut:
        return cls.model_validate(contact, from_attributes=True)


class TravellerIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    full_name_as_passport: Annotated[str | None, Field(default=None, max_length=200)]
    dob: date | None = None
    gender: Annotated[str | None, Field(default=None, max_length=20)]
    passport_no: Annotated[str | None, Field(default=None, max_length=80)]
    passport_expiry: date | None = None
    nationality: Annotated[str | None, Field(default=None, min_length=2, max_length=2)]
    is_minor: StrictBool = False
    meal_pref: Annotated[str | None, Field(default=None, max_length=80)]
    seat_pref: Annotated[str | None, Field(default=None, max_length=80)]


class TravellerOut(BaseModel):
    id: UUID
    customer_id: UUID
    full_name_as_passport: str | None
    dob: date | None
    gender: str | None
    passport_no: str | None
    passport_expiry: date | None
    nationality: str | None
    is_minor: bool
    meal_pref: str | None
    seat_pref: str | None

    @classmethod
    def from_model(cls, traveller: Traveller) -> TravellerOut:
        return cls.model_validate(traveller, from_attributes=True)


@router.post("/consents", response_model=ConsentOut, status_code=status.HTTP_201_CREATED)
def grant_consent(
    payload: ConsentGrantIn,
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
) -> ConsentOut:
    try:
        consent = ConsentService(audit).grant(
            db,
            contact_id=payload.contact_id,
            channel=payload.channel,
            purpose=payload.purpose,
            source=payload.source,
            proof_ref=payload.proof_ref,
            actor=Actor.user(auth.user.id),
            tenant_id=auth.user.tenant_id,
        )
    except CRMRecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="contact_not_found") from exc
    except CRMValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return ConsentOut.from_model(consent)


@router.post("/consents/{consent_id}/withdraw", response_model=ConsentOut)
def withdraw_consent(
    consent_id: UUID,
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
) -> ConsentOut:
    try:
        consent = ConsentService(audit).withdraw(
            db,
            consent_id=consent_id,
            actor=Actor.user(auth.user.id),
            tenant_id=auth.user.tenant_id,
        )
    except CRMRecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="consent_not_found") from exc
    db.commit()
    return ConsentOut.from_model(consent)


@router.get("/contacts/{contact_id}/can-send", response_model=CanSendOut)
def can_send(
    contact_id: UUID,
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_READ))],
    db: Annotated[Session, Depends(get_db)],
    channel: Annotated[str, Query(min_length=2, max_length=20)],
    purpose: Annotated[str, Query(min_length=2, max_length=20)],
) -> CanSendOut:
    try:
        allowed = ConsentService().can_send(
            db,
            contact_id=contact_id,
            channel=channel,
            purpose=purpose,
            tenant_id=auth.user.tenant_id,
        )
    except CRMRecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="contact_not_found") from exc
    return CanSendOut(can_send=allowed)


@router.post("/customers", response_model=CustomerOut, status_code=status.HTTP_201_CREATED)
def create_customer(
    payload: CustomerIn,
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
) -> CustomerOut:
    try:
        customer = CRMService(audit).create_customer(
            db,
            customer_type=payload.customer_type,
            display_name=payload.display_name,
            segment=payload.segment,
            actor=Actor.user(auth.user.id),
            tenant_id=auth.user.tenant_id,
        )
    except CRMValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return CustomerOut.from_model(customer)


@router.get("/customers", response_model=list[CustomerOut])
def list_customers(
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_READ))],
    db: Annotated[Session, Depends(get_db)],
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[CustomerOut]:
    customers = CRMService().list_customers(
        db, tenant_id=auth.user.tenant_id, limit=limit, offset=offset
    )
    return [CustomerOut.from_model(customer) for customer in customers]


@router.get("/customers/{customer_id}", response_model=CustomerOut)
def get_customer(
    customer_id: UUID,
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_READ))],
    db: Annotated[Session, Depends(get_db)],
) -> CustomerOut:
    try:
        customer = CRMService().get_customer(
            db, customer_id=customer_id, tenant_id=auth.user.tenant_id
        )
    except CRMRecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="customer_not_found") from exc
    return CustomerOut.from_model(customer)


@router.post(
    "/customers/{customer_id}/contacts",
    response_model=ContactOut,
    status_code=status.HTTP_201_CREATED,
)
def create_contact(
    customer_id: UUID,
    payload: ContactIn,
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
) -> ContactOut:
    try:
        contact = CRMService(audit).create_contact(
            db,
            customer_id=customer_id,
            channel=payload.channel,
            value=payload.value,
            verified=payload.verified,
            is_primary=payload.is_primary,
            language=payload.language,
            actor=Actor.user(auth.user.id),
            tenant_id=auth.user.tenant_id,
        )
    except CRMRecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="customer_not_found") from exc
    except CRMValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return ContactOut.from_model(contact)


@router.get("/customers/{customer_id}/contacts", response_model=list[ContactOut])
def list_contacts(
    customer_id: UUID,
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_READ))],
    db: Annotated[Session, Depends(get_db)],
) -> list[ContactOut]:
    try:
        contacts = CRMService().list_contacts(
            db, customer_id=customer_id, tenant_id=auth.user.tenant_id
        )
    except CRMRecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="customer_not_found") from exc
    return [ContactOut.from_model(contact) for contact in contacts]


@router.post(
    "/customers/{customer_id}/travellers",
    response_model=TravellerOut,
    status_code=status.HTTP_201_CREATED,
)
def create_traveller(
    customer_id: UUID,
    payload: TravellerIn,
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
) -> TravellerOut:
    try:
        traveller = CRMService(audit).create_traveller(
            db,
            customer_id=customer_id,
            **payload.model_dump(),
            actor=Actor.user(auth.user.id),
            tenant_id=auth.user.tenant_id,
        )
    except CRMRecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="customer_not_found") from exc
    except CRMValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return TravellerOut.from_model(traveller)


@router.get("/customers/{customer_id}/travellers", response_model=list[TravellerOut])
def list_travellers(
    customer_id: UUID,
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_READ))],
    db: Annotated[Session, Depends(get_db)],
) -> list[TravellerOut]:
    try:
        travellers = CRMService().list_travellers(
            db, customer_id=customer_id, tenant_id=auth.user.tenant_id
        )
    except CRMRecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="customer_not_found") from exc
    return [TravellerOut.from_model(traveller) for traveller in travellers]
