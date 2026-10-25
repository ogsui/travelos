"""Authenticated CRM communication timeline endpoint."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel
from sqlalchemy.orm import Session

from travelos.api.deps import AuthContext, get_db, require
from travelos.channels.service import ChannelService
from travelos.security.permissions import Permission

router = APIRouter(prefix="/crm", tags=["channels"])


class TimelineMessageOut(BaseModel):
    id: UUID
    conversation_id: UUID
    customer_id: UUID | None
    contact_id: UUID | None
    channel: str
    direction: Literal["inbound", "outbound"]
    body: str
    media_refs: list[str]
    template_id: str | None
    status: str
    occurred_at: datetime


@router.get("/timeline", response_model=list[TimelineMessageOut])
def get_timeline(
    auth: Annotated[AuthContext, Depends(require(Permission.CRM_READ))],
    db: Annotated[Session, Depends(get_db)],
    customer_id: UUID | None = None,
    contact_id: UUID | None = None,
    channel: Annotated[str | None, Query(min_length=2, max_length=20)] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
) -> list[TimelineMessageOut]:
    messages = ChannelService.timeline(
        db,
        tenant_id=auth.user.tenant_id,
        customer_id=customer_id,
        contact_id=contact_id,
        channel=channel,
        limit=limit,
    )
    return [
        TimelineMessageOut(
            id=message.id,
            conversation_id=message.conversation_id,
            customer_id=message.conversation.customer_id if message.conversation else None,
            contact_id=message.conversation.contact_id if message.conversation else None,
            channel=message.channel,
            direction=message.direction,
            body=message.body,
            media_refs=message.media_refs,
            template_id=message.template_id,
            status=message.status,
            occurred_at=message.occurred_at,
        )
        for message in messages
    ]
