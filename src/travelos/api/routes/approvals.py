"""Approval and autonomy API.

Any module can ask ``POST /approvals`` whether it may act. The response tells
it ``auto`` (proceed), ``needs_approval`` (a request was opened) or an error for
``deny``. Autonomy settings are readable by operators and writable only by the
owner; LOCKED actions are rejected by the service even if the caller is owner.
"""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from sqlalchemy.orm import Session

from travelos.api.deps import (
    AuthContext,
    client_ip,
    get_approval_service,
    get_db,
    require,
)
from travelos.api.schemas import (
    ApprovalDecisionRequest,
    ApprovalOut,
    ApprovalSubmitRequest,
    ApprovalSubmitResponse,
    AutonomyPolicyOut,
    AutonomyPolicyUpdate,
    PolicyDecisionOut,
)
from travelos.approval.errors import (
    ApprovalError,
    ApprovalNotFoundError,
    ApprovalPermissionError,
    ApprovalStateError,
    DistinctReviewerError,
    EditRequiresChangeError,
    PolicyDeniedError,
    PolicyViolationError,
    ReasonRequiredError,
    SelfApprovalError,
    UnknownActionError,
)
from travelos.approval.matrix import ActionSpec, PolicyDecision, get_spec
from travelos.approval.service import ApprovalService
from travelos.core.models import AutonomyPolicy
from travelos.security.permissions import Permission

router = APIRouter(prefix="/approvals", tags=["approvals"])
autonomy_router = APIRouter(prefix="/autonomy", tags=["autonomy"])

_ERROR_STATUS: dict[type[ApprovalError], tuple[int, str]] = {
    ApprovalNotFoundError: (status.HTTP_404_NOT_FOUND, "approval_not_found"),
    UnknownActionError: (status.HTTP_404_NOT_FOUND, "unknown_action"),
    PolicyDeniedError: (status.HTTP_403_FORBIDDEN, "autonomy_denied"),
    SelfApprovalError: (status.HTTP_403_FORBIDDEN, "self_approval_forbidden"),
    ApprovalPermissionError: (status.HTTP_403_FORBIDDEN, "approval_permission_denied"),
    PolicyViolationError: (status.HTTP_409_CONFLICT, "policy_locked"),
    ApprovalStateError: (status.HTTP_409_CONFLICT, "approval_not_pending"),
    DistinctReviewerError: (status.HTTP_409_CONFLICT, "distinct_reviewer_required"),
    ReasonRequiredError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "reason_required"),
    EditRequiresChangeError: (status.HTTP_422_UNPROCESSABLE_CONTENT, "edit_requires_change"),
}


def _http_error(exc: ApprovalError) -> HTTPException:
    for kind, (code, detail) in _ERROR_STATUS.items():
        if isinstance(exc, kind):
            return HTTPException(status_code=code, detail=detail)
    return HTTPException(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        detail="approval_error",
    )


def _decision_out(decision: PolicyDecision) -> PolicyDecisionOut:
    return PolicyDecisionOut(
        action_type=decision.action_type,
        decision=decision.decision.value,
        level=decision.level.value,
        configured_level=decision.configured_level.value,
        max_level=decision.max_level.value,
        locked=decision.locked,
        owner_only=decision.owner_only,
        requires_four_eyes=decision.requires_four_eyes,
        review_after=decision.review_after,
        reason=decision.reason,
    )


def _policy_out(
    spec: ActionSpec,
    row: AutonomyPolicy | None,
    decision: PolicyDecision,
) -> AutonomyPolicyOut:
    return AutonomyPolicyOut(
        action_type=spec.action_type,
        level=decision.level.value,
        default_level=spec.default_level.value,
        max_level=spec.max_level.value,
        locked=spec.locked,
        owner_only=spec.owner_only,
        may_graduate=spec.may_graduate,
        value_threshold_paise=int(row.value_threshold_paise) if row is not None else 0,
        currency=row.currency if row is not None else "INR",
        requires_four_eyes=bool(row.requires_four_eyes) if row is not None else False,
        notes=(row.notes if row is not None else spec.notes) or None,
        source="database" if row is not None else "catalogue",
    )


@router.post("", response_model=ApprovalSubmitResponse)
def submit_approval(
    payload: ApprovalSubmitRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require(Permission.APPROVALS_REQUEST))],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[ApprovalService, Depends(get_approval_service)],
) -> ApprovalSubmitResponse:
    try:
        submission = service.submit(
            db,
            action_type=payload.action_type,
            subject_type=payload.subject_type,
            subject_id=payload.subject_id,
            proposed=payload.proposed,
            requested_by=auth.user.id,
            reason=payload.reason,
            value_paise=payload.value_paise,
            context=payload.context,
            tenant_id=auth.user.tenant_id,
            ip=client_ip(request),
        )
        db.commit()
    except ApprovalError as exc:
        db.rollback()
        raise _http_error(exc) from exc

    approval = submission.approval
    if approval is not None:
        db.refresh(approval)
    return ApprovalSubmitResponse(
        decision=_decision_out(submission.decision),
        approval=ApprovalOut.model_validate(approval, from_attributes=True)
        if approval is not None
        else None,
    )


@router.get("", response_model=list[ApprovalOut])
def list_approvals(
    auth: Annotated[AuthContext, Depends(require(Permission.APPROVALS_READ))],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[ApprovalService, Depends(get_approval_service)],
    request_status: Annotated[str | None, Query(alias="status", max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
) -> list[ApprovalOut]:
    rows = service.list_requests(
        db, status=request_status, tenant_id=auth.user.tenant_id, limit=limit
    )
    return [ApprovalOut.model_validate(row, from_attributes=True) for row in rows]


@router.get("/{request_id}", response_model=ApprovalOut)
def get_approval(
    request_id: UUID,
    auth: Annotated[AuthContext, Depends(require(Permission.APPROVALS_READ))],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[ApprovalService, Depends(get_approval_service)],
) -> ApprovalOut:
    try:
        row = service.get(db, request_id, tenant_id=auth.user.tenant_id)
    except ApprovalError as exc:
        raise _http_error(exc) from exc
    return ApprovalOut.model_validate(row, from_attributes=True)


@router.post("/{request_id}/decision", response_model=ApprovalOut)
def decide_approval(
    request_id: UUID,
    payload: ApprovalDecisionRequest,
    request: Request,
    auth: Annotated[AuthContext, Depends(require(Permission.APPROVALS_DECIDE))],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[ApprovalService, Depends(get_approval_service)],
) -> ApprovalOut:
    try:
        row = service.decide(
            db,
            request_id=request_id,
            reviewer_id=auth.user.id,
            reviewer_role=auth.user.role,
            decision=payload.decision,
            final=payload.final,
            reason=payload.reason,
            tenant_id=auth.user.tenant_id,
            ip=client_ip(request),
        )
        db.commit()
    except ApprovalError as exc:
        db.rollback()
        raise _http_error(exc) from exc
    db.refresh(row)
    return ApprovalOut.model_validate(row, from_attributes=True)


@autonomy_router.get("/policies", response_model=list[AutonomyPolicyOut])
def list_policies(
    auth: Annotated[AuthContext, Depends(require(Permission.AUTONOMY_READ))],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[ApprovalService, Depends(get_approval_service)],
) -> list[AutonomyPolicyOut]:
    states = service.autonomy.list_states(db, tenant_id=auth.user.tenant_id)
    return [_policy_out(spec, row, decision) for spec, row, decision in states]


@autonomy_router.put("/policies/{action_type}", response_model=AutonomyPolicyOut)
def update_policy(
    action_type: str,
    payload: AutonomyPolicyUpdate,
    request: Request,
    auth: Annotated[AuthContext, Depends(require(Permission.AUTONOMY_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    service: Annotated[ApprovalService, Depends(get_approval_service)],
) -> AutonomyPolicyOut:
    try:
        row = service.autonomy.update_policy(
            db,
            action_type=action_type,
            level=payload.level,
            value_threshold_paise=payload.value_threshold_paise,
            requires_four_eyes=payload.requires_four_eyes,
            notes=payload.notes,
            actor_id=auth.user.id,
            actor_role=auth.user.role,
            tenant_id=auth.user.tenant_id,
        )
        db.commit()
    except ApprovalError as exc:
        db.rollback()
        raise _http_error(exc) from exc

    db.refresh(row)
    spec = get_spec(action_type)
    decision = service.autonomy.decision_for(db, action_type, tenant_id=auth.user.tenant_id)
    return _policy_out(spec, row, decision)
