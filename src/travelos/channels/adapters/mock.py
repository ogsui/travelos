"""Deterministic fixture adapter. It persists inbound events only."""

from __future__ import annotations

from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from travelos.channels.models import Message
from travelos.channels.service import ChannelService, InboundMessage, InboundResult
from travelos.core.db import DEFAULT_TENANT_ID


class ChannelAdapter(Protocol):
    def receive(
        self,
        db: Session,
        *,
        event: InboundMessage,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        customer_id: UUID | None = None,
        contact_id: UUID | None = None,
    ) -> InboundResult: ...

    def send(self, message: Message) -> None: ...


class OutboundDisabledError(RuntimeError):
    """The fixture adapter has no transport and never delivers externally."""


class MockChannelAdapter:
    def __init__(self) -> None:
        self._service = ChannelService()

    def receive(
        self,
        db: Session,
        *,
        event: InboundMessage,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        customer_id: UUID | None = None,
        contact_id: UUID | None = None,
    ) -> InboundResult:
        return self._service.receive(
            db,
            event=event,
            tenant_id=tenant_id,
            customer_id=customer_id,
            contact_id=contact_id,
        )

    @staticmethod
    def send(_message: Message) -> None:
        raise OutboundDisabledError("mock channel has no outbound transport")

    @staticmethod
    def status(_external_id: str) -> str:
        return "unavailable"

    @staticmethod
    def templates() -> tuple[str, ...]:
        return ()

    def timeline(self, db: Session, **filters: object) -> list[Message]:
        return self._service.timeline(db, **filters)  # type: ignore[arg-type]
