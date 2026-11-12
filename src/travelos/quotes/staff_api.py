"""Staff-facing quote endpoints (task 016c2c2).

The only route here is the owner price override. Recording an override *is* the
owner's decision on a below-floor or uncompetitive price, and the approval matrix
keeps that row LOCKED A0, so the endpoint requires ``quotes:price_override``,
which no other role holds. The service repeats the owner check against the user
row, so a misconfigured role matrix still cannot let another role decide a price.

The route only records a decision. It does not send anything, build a quote,
move money or change a calculation in place: the service writes an append-only
override plus the deterministic replacement calculation, and a later quote
revision must cite both.

Errors are stable for the dashboard: an unknown calculation or a calculation
from another tenant is ``404 not_found``; every other refusal is ``409`` with
the service's actionable explanation (never customer or supplier data).
"""

from __future__ import annotations

from datetime import datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from travelos.api.deps import AuthContext, get_audit_writer, get_db, require
from travelos.core.audit import Actor, AuditWriter
from travelos.core.money import Money
from travelos.quotes.errors import (
    QuotePriceCalculationNotFoundError,
    QuotePriceOverrideError,
)
from travelos.quotes.overrides import QuotePriceOverrideService
from travelos.security.permissions import Permission

router = APIRouter(prefix="/quotes", tags=["quotes"])


class PriceOverrideIn(BaseModel):
    """The owner's decision: which price, which total, and why."""

    model_config = ConfigDict(extra="forbid")

    calculation_id: UUID
    owner_total_paise: Annotated[int, Field(ge=0)]
    reason: Annotated[str, Field(min_length=5, max_length=2_000)]
    decision_task_id: UUID | None = None


class PriceOverrideOut(BaseModel):
    """The recorded decision. No reason text, supplier cost or contact data."""

    override_id: UUID
    source_calculation_id: UUID
    authorised_calculation_id: UUID
    decision_task_id: UUID
    status: str
    owner_total_paise: int
    currency: str
    decided_at: datetime


def _now(request: Request) -> datetime | None:
    """The app's clock when a deployment or test installs one."""

    clock = getattr(request.app.state, "quote_clock", None)
    return clock() if clock is not None else None


@router.post(
    "/price-overrides",
    response_model=PriceOverrideOut,
    status_code=status.HTTP_201_CREATED,
)
def record_price_override(
    payload: PriceOverrideIn,
    request: Request,
    auth: Annotated[AuthContext, Depends(require(Permission.QUOTES_PRICE_OVERRIDE))],
    db: Annotated[Session, Depends(get_db)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
) -> PriceOverrideOut:
    """Authorise one non-``ok`` calculation at the owner's total."""

    service = QuotePriceOverrideService(
        db,
        tenant_id=auth.user.tenant_id,
        audit=audit,
    )
    now = _now(request)
    try:
        row = service.record(
            payload.calculation_id,
            owner_total=Money(payload.owner_total_paise),
            reason=payload.reason,
            actor=Actor.user(auth.user.id),
            task_id=payload.decision_task_id,
            now=now,
        )
    except QuotePriceCalculationNotFoundError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found") from exc
    except QuotePriceOverrideError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=str(exc),
        ) from exc
    db.commit()
    return PriceOverrideOut(
        override_id=row.id,
        source_calculation_id=row.source_calculation_id,
        authorised_calculation_id=row.authorised_calculation_id,
        decision_task_id=row.decision_task_id,
        status=row.status,
        owner_total_paise=row.owner_total_paise,
        currency=row.currency,
        decided_at=row.decided_at,
    )


__all__ = ["PriceOverrideIn", "PriceOverrideOut", "router"]
