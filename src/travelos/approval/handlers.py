"""Outbox handlers that let approvals react to timer events.

The worker registers :func:`handle_timer_fired` for ``timer.fired``. Approval
timers are routed here; any other timer name raises, so the dispatcher
dead-letters it instead of silently dropping a signal another module expects.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from travelos.approval.service import ApprovalService
from travelos.core.config import Settings, get_settings
from travelos.core.models import OutboxEvent


def handle_timer_fired(db: Session, event: OutboxEvent) -> None:
    settings: Settings = get_settings()
    ApprovalService.from_settings(settings).handle_timer(db, event.payload)
