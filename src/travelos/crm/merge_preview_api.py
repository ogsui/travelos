"""CRM customer merge preview and approval request API."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from travelos.api.deps import AuthContext, client_ip, get_approval_service, get_db, require
from travelos.approval.service import ApprovalService
from travelos.core.audit import Actor
from travelos.crm.errors import CRMRecordNotFoundError, CRMValidationError
from travelos.crm.merge_preview import CustomerMergePreviewService
from travelos.crm.merge_service import CustomerMergeService
from travelos.security.permissions import Permission

router = APIRouter()


class CustomerMergeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_customer_id: UUID
    target_customer_id: UUID


class CustomerMergeOverlapOut(BaseModel):
    channel: str
    count: int


class CustomerMergePreviewOut(BaseModel):
    source_customer_id: UUID
    source_display_name: str
    target_customer_id: UUID
    target_display_name: str
    source_contacts: int
    target_contacts: int
    source_travellers: int
    target_travellers: int
    source_consents: int
    after_target_contacts: int
    after_target_travellers: int
    overlapping_contacts: list[CustomerMergeOverlapOut]
    approval_id: UUID
    approval_status: str


class CustomerMergeApplyOut(BaseModel):
    approval_id: UUID
    source_customer_id: UUID
    target_customer_id: UUID
    moved_contacts: int
    moved_travellers: int
    already_applied: bool


@router.post("/merges/preview", response_model=CustomerMergePreviewOut)
def preview_customer_merge(
    payload: CustomerMergeIn,
    request: Request,
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    approvals: Annotated[ApprovalService, Depends(get_approval_service)],
) -> CustomerMergePreviewOut:
    try:
        preview = CustomerMergePreviewService().preview(
            db,
            source_customer_id=payload.source_customer_id,
            target_customer_id=payload.target_customer_id,
            approvals=approvals,
            actor=Actor.user(auth.user.id),
            tenant_id=auth.user.tenant_id,
            ip=client_ip(request),
        )
    except CRMRecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="customer_not_found") from exc
    except CRMValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    approval = preview.submission.approval
    if approval is None:
        raise HTTPException(status_code=409, detail="customer merge policy must require approval")
    db.commit()
    return CustomerMergePreviewOut(
        source_customer_id=preview.source.id,
        source_display_name=preview.source.display_name,
        target_customer_id=preview.target.id,
        target_display_name=preview.target.display_name,
        source_contacts=preview.source_contacts,
        target_contacts=preview.target_contacts,
        source_travellers=preview.source_travellers,
        target_travellers=preview.target_travellers,
        source_consents=preview.source_consents,
        after_target_contacts=preview.target_contacts + preview.source_contacts,
        after_target_travellers=preview.target_travellers + preview.source_travellers,
        overlapping_contacts=[
            CustomerMergeOverlapOut(channel=channel, count=count)
            for channel, count in preview.overlapping_contacts
        ],
        approval_id=approval.id,
        approval_status=approval.status,
    )


@router.post("/merges/apply/{approval_id}", response_model=CustomerMergeApplyOut)
def apply_customer_merge(
    approval_id: UUID,
    request: Request,
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_WRITE))],
    db: Annotated[Session, Depends(get_db)],
) -> CustomerMergeApplyOut:
    try:
        result = CustomerMergeService().apply_approved(
            db,
            approval_id=approval_id,
            actor=Actor.user(auth.user.id),
            tenant_id=auth.user.tenant_id,
            ip=client_ip(request),
        )
    except CRMRecordNotFoundError as exc:
        raise HTTPException(status_code=404, detail="approval_not_found") from exc
    except CRMValidationError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    db.commit()
    return CustomerMergeApplyOut.model_validate(result, from_attributes=True)
