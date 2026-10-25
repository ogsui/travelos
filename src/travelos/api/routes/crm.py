"""CRM consent API."""

from __future__ import annotations

import base64
import binascii
from datetime import date, datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool
from sqlalchemy.orm import Session

from travelos.api.deps import (
    AuthContext,
    client_ip,
    get_approval_service,
    get_audit_writer,
    get_db,
    require,
)
from travelos.approval.service import ApprovalService
from travelos.core.audit import Actor, AuditWriter
from travelos.crm.consent import ConsentService
from travelos.crm.errors import CRMRecordNotFoundError, CRMValidationError
from travelos.crm.import_service import CRMImportService
from travelos.crm.importer import (
    CRMImportFormatError,
    CRMImportMapping,
    CRMImportReport,
)
from travelos.crm.merge_preview_api import router as merge_preview_router
from travelos.crm.models import Consent, Contact, Customer, Traveller
from travelos.crm.service import CRMService
from travelos.security.permissions import Permission

router = APIRouter(prefix="/crm", tags=["crm"])
router.include_router(merge_preview_router)


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


class CRMImportMappingIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: Annotated[str, Field(min_length=1, max_length=120)]
    customer_type: Annotated[str | None, Field(default=None, max_length=120)]
    segment: Annotated[str | None, Field(default=None, max_length=120)]
    email: Annotated[str | None, Field(default=None, max_length=120)]
    phone: Annotated[str | None, Field(default=None, max_length=120)]
    whatsapp: Annotated[str | None, Field(default=None, max_length=120)]
    email_marketing_consent: Annotated[str | None, Field(default=None, max_length=120)]
    email_marketing_proof_ref: Annotated[str | None, Field(default=None, max_length=120)]
    phone_marketing_consent: Annotated[str | None, Field(default=None, max_length=120)]
    phone_marketing_proof_ref: Annotated[str | None, Field(default=None, max_length=120)]
    whatsapp_marketing_consent: Annotated[str | None, Field(default=None, max_length=120)]
    whatsapp_marketing_proof_ref: Annotated[str | None, Field(default=None, max_length=120)]

    def to_domain(self) -> CRMImportMapping:
        return CRMImportMapping(**self.model_dump())


class CRMImportIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    filename: Annotated[str, Field(min_length=5, max_length=255)]
    content_base64: Annotated[SecretStr, Field(min_length=4, max_length=14_000_000)]
    mapping: CRMImportMappingIn


class CRMImportRowIssueOut(BaseModel):
    row_number: int
    code: str


class CRMImportDuplicateOut(BaseModel):
    channel: str
    first_row: int | None
    duplicate_row: int
    existing_customer: bool


class CRMImportPreviewOut(BaseModel):
    source_hash: str
    mapping_hash: str
    batch_key: str
    rows_seen: int
    valid_rows: int
    importable_rows: int
    bad_rows: list[CRMImportRowIssueOut]
    duplicate_candidates: list[CRMImportDuplicateOut]
    unclear_consent_rows: list[int]
    approval_id: UUID
    approval_status: str


class CRMImportResultOut(BaseModel):
    imported_customers: int
    imported_contacts: int
    granted_consents: int
    duplicate_rows_skipped: int
    already_applied: bool


def _decode_import(payload: CRMImportIn) -> bytes:
    try:
        return base64.b64decode(payload.content_base64.get_secret_value(), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise HTTPException(status_code=422, detail="content_base64 must be valid base64") from exc


def _preview_out(
    report: CRMImportReport, approval_id: UUID, status_value: str
) -> CRMImportPreviewOut:
    return CRMImportPreviewOut(
        source_hash=report.source_hash,
        mapping_hash=report.mapping_hash,
        batch_key=report.batch_key,
        rows_seen=report.rows_seen,
        valid_rows=len(report.valid_rows),
        importable_rows=len(report.importable_rows),
        bad_rows=[CRMImportRowIssueOut.model_validate(row) for row in report.bad_rows],
        duplicate_candidates=[
            CRMImportDuplicateOut(
                channel=row.channel,
                first_row=row.first_row,
                duplicate_row=row.duplicate_row,
                existing_customer=row.existing_customer_id is not None,
            )
            for row in report.duplicate_candidates
        ],
        unclear_consent_rows=list(report.unclear_consent_rows),
        approval_id=approval_id,
        approval_status=status_value,
    )


@router.post("/imports/preview", response_model=CRMImportPreviewOut)
def preview_customer_import(
    payload: CRMImportIn,
    request: Request,
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    approvals: Annotated[ApprovalService, Depends(get_approval_service)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
) -> CRMImportPreviewOut:
    service = CRMImportService(audit)
    try:
        report = service.preview(
            db,
            content=_decode_import(payload),
            filename=payload.filename,
            mapping=payload.mapping.to_domain(),
            tenant_id=auth.user.tenant_id,
        )
        submission = service.request_approval(
            db,
            report=report,
            approvals=approvals,
            actor=Actor.user(auth.user.id),
            tenant_id=auth.user.tenant_id,
            ip=client_ip(request),
        )
    except (CRMImportFormatError, CRMValidationError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if submission.approval is None:
        raise HTTPException(status_code=409, detail="bulk CRM import policy must require approval")
    db.commit()
    return _preview_out(report, submission.approval.id, submission.approval.status)


@router.post("/imports/apply/{approval_id}", response_model=CRMImportResultOut)
def apply_customer_import(
    approval_id: UUID,
    payload: CRMImportIn,
    request: Request,
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
) -> CRMImportResultOut:
    try:
        result = CRMImportService(audit).apply_approved(
            db,
            content=_decode_import(payload),
            filename=payload.filename,
            mapping=payload.mapping.to_domain(),
            approval_id=approval_id,
            actor=Actor.user(auth.user.id),
            tenant_id=auth.user.tenant_id,
            ip=client_ip(request),
        )
    except CRMValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CRMImportFormatError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    db.commit()
    return CRMImportResultOut.model_validate(result, from_attributes=True)


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
