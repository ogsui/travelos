"""CRM consent API."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from travelos.api.deps import AuthContext, get_audit_writer, get_db, require
from travelos.core.audit import Actor, AuditWriter
from travelos.crm.consent import ConsentService
from travelos.crm.errors import CRMRecordNotFoundError, CRMValidationError
from travelos.crm.models import Consent
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
