"""Owner-only kill-switch administration.

Flags are stored in ``feature_flags`` (per tenant, scope ``global``) and are
also constrained by the environment kill switch: turning a capability off in
the environment cannot be undone from the API.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.api.deps import (
    AuthContext,
    client_ip,
    get_audit_writer,
    get_db,
    get_feature_flags,
    require,
)
from travelos.api.schemas import FlagOut, FlagUpdate
from travelos.core.audit import Actor, AuditWriter
from travelos.core.config import KillSwitches
from travelos.core.features import DEFAULT_SCOPE, FeatureFlagService
from travelos.core.models import FeatureFlag
from travelos.security.permissions import Permission

router = APIRouter(prefix="/admin", tags=["admin"])

_KNOWN_SWITCHES = frozenset(KillSwitches.switch_names())


@router.get("/flags", response_model=list[FlagOut])
def list_flags(
    auth: Annotated[AuthContext, Depends(require(Permission.FLAGS_READ))],
    flags: Annotated[FeatureFlagService, Depends(get_feature_flags)],
) -> list[FlagOut]:
    result: list[FlagOut] = []
    for key in sorted(_KNOWN_SWITCHES):
        state = flags.state(key, tenant_id=auth.user.tenant_id)
        result.append(
            FlagOut(key=key, enabled=state.enabled, source=state.source, scope=DEFAULT_SCOPE)
        )
    return result


@router.put("/flags/{key}", response_model=FlagOut)
def update_flag(
    key: str,
    payload: FlagUpdate,
    request: Request,
    auth: Annotated[AuthContext, Depends(require(Permission.FLAGS_WRITE))],
    db: Annotated[Session, Depends(get_db)],
    flags: Annotated[FeatureFlagService, Depends(get_feature_flags)],
    audit: Annotated[AuditWriter, Depends(get_audit_writer)],
) -> FlagOut:
    if key not in _KNOWN_SWITCHES:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown_kill_switch")

    row = (
        db.execute(
            select(FeatureFlag).where(
                FeatureFlag.tenant_id == auth.user.tenant_id,
                FeatureFlag.key == key,
                FeatureFlag.scope == DEFAULT_SCOPE,
            )
        )
        .scalars()
        .first()
    )
    before = dict(row.value) if row is not None and isinstance(row.value, dict) else {}
    if row is None:
        row = FeatureFlag(
            tenant_id=auth.user.tenant_id,
            key=key,
            scope=DEFAULT_SCOPE,
            value={"enabled": payload.enabled},
            updated_by=auth.user.id,
        )
        db.add(row)
    else:
        row.value = {"enabled": payload.enabled}
        row.updated_by = auth.user.id

    # Flush so a newly created row has its UUID before it is referenced by the
    # audit entry (ids are generated Python-side at INSERT time).
    db.flush()
    audit.record_change(
        db,
        action="flags.updated",
        actor=Actor.user(auth.user.id),
        subject_type="feature_flag",
        subject_id=row.id,
        before=before,
        after={"enabled": payload.enabled},
        ip=client_ip(request),
    )
    db.commit()
    flags.invalidate(key)
    state = flags.state(key, tenant_id=auth.user.tenant_id)
    return FlagOut(key=key, enabled=state.enabled, source=state.source, scope=DEFAULT_SCOPE)
