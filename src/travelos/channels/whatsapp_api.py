"""Meta WhatsApp Business Cloud API webhook endpoints."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel
from sqlalchemy.orm import Session

from travelos.api.deps import get_app_settings, get_db
from travelos.channels.adapters.whatsapp import (
    WhatsAppChannelAdapter,
    WhatsAppPayloadError,
    verify_meta_signature,
    verify_webhook_challenge,
)
from travelos.core.config import Mode, Settings
from travelos.core.db import DEFAULT_TENANT_ID

logger = logging.getLogger("travelos.channels.whatsapp_api")
router = APIRouter(prefix="/channels/whatsapp", tags=["channels"])


def _extract_secret(value: object) -> str:
    if value is None:
        return ""
    if hasattr(value, "get_secret_value"):
        return str(value.get_secret_value())
    return str(value)


def _require_secret(settings: Settings, value: object, name: str) -> str:
    """Return a configured webhook secret, or fail closed outside test mode."""
    secret = _extract_secret(value)
    if secret:
        return secret
    if settings.mode is Mode.TEST:
        return ""
    logger.error("WhatsApp webhook rejected: %s is not configured", name)
    raise HTTPException(
        status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
        detail="whatsapp_webhook_not_configured",
    )


class WhatsAppWebhookResponse(BaseModel):
    status: str
    received: int


@router.get("/webhook")
def verify_webhook(
    settings: Annotated[Settings, Depends(get_app_settings)],
    hub_mode: Annotated[str | None, Query(alias="hub.mode")] = None,
    hub_verify_token: Annotated[str | None, Query(alias="hub.verify_token")] = None,
    hub_challenge: Annotated[str | None, Query(alias="hub.challenge")] = None,
) -> Response:
    """Handle Meta's webhook subscription verification handshake."""
    expected_token = _require_secret(
        settings, settings.whatsapp_verify_token, "WHATSAPP_VERIFY_TOKEN"
    )

    challenge = verify_webhook_challenge(
        mode=hub_mode,
        token=hub_verify_token,
        challenge=hub_challenge,
        expected_token=expected_token,
    )

    if challenge is None:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="verification_failed",
        )

    return PlainTextResponse(content=challenge, status_code=status.HTTP_200_OK)


@router.post("/webhook", response_model=WhatsAppWebhookResponse)
async def receive_webhook(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_app_settings)],
    x_hub_signature_256: Annotated[str | None, Header()] = None,
) -> WhatsAppWebhookResponse:
    """Receive and ingest inbound WhatsApp Cloud API webhook events."""
    raw_body = await request.body()

    # Signature verification. An unset app secret is tolerated only in test mode;
    # shadow and live fail closed rather than trusting anonymous webhook traffic.
    secret = _require_secret(settings, settings.whatsapp_app_secret, "WHATSAPP_APP_SECRET")
    if secret and not verify_meta_signature(raw_body, x_hub_signature_256, secret):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid_signature",
        )

    adapter = WhatsAppChannelAdapter(
        mode=settings.mode,
        kill_switch_enabled=settings.kill_switches.whatsapp,
    )

    try:
        results = adapter.receive_webhook(
            db,
            payload=raw_body,
            tenant_id=DEFAULT_TENANT_ID,
        )
        db.commit()
    except WhatsAppPayloadError as exc:
        raise HTTPException(
            status_code=422,
            detail=str(exc),
        ) from exc
    except Exception:
        db.rollback()
        logger.exception("Failed to process WhatsApp webhook payload")
        raise

    return WhatsAppWebhookResponse(status="ok", received=len(results))
