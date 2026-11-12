"""Public quote link endpoints (task 016c2b).

A customer opens a link that carries the random token issued in 016c1. The token
is the credential: these routes have no staff session, no cookie and no CSRF
check, and they resolve the quote by token digest only. An unknown, malformed or
ambiguous token is always ``404`` so the endpoint cannot be used to probe for
quotes.

Three operations, each rate-limited per source address:

* ``GET /public/quotes/{token}`` - the customer-safe status: state, revision,
  total, currency, expiry and whether acceptance is still possible. Opening it
  records the first view (016c1) and nothing else.
* ``GET /public/quotes/{token}/document`` - the rendered customer document. It
  uses the same owner presentation config as every other document, so an
  incomplete config is ``503`` rather than invented wording, and an expired quote
  is refused rather than shown as if it were still valid.
* ``POST /public/quotes/{token}/accept`` - accepts the exact revision the
  customer names. A stale revision, an elapsed window, an unsent quote or a
  second acceptance is ``409`` with a stable reason.

No response carries the token, its digest, a contact value, supplier cost,
margin or any internal id. Wording for buttons and emails lives in the public
site, not here.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.orm import Session

from travelos.api.deps import client_ip, get_db
from travelos.channels.web_form import RateLimitStore
from travelos.crm.fingerprints import ContactFingerprinter
from travelos.quotes.acceptance import QuoteAcceptanceService
from travelos.quotes.errors import (
    QuoteAcceptanceError,
    QuoteLinkNotFoundError,
    QuotePresentationError,
)
from travelos.quotes.models import AcceptanceStatus
from travelos.quotes.presentation import DEFAULT_PRESENTATION_PATH
from travelos.quotes.records import QuoteRecord
from travelos.quotes.rendering import QuoteRenderer

router = APIRouter(prefix="/public/quotes", tags=["public-quotes"])

_RATE_LIMIT_PURPOSE = "quote-link-source-ip"


class QuoteLinkOut(BaseModel):
    """What a link holder may see. No ids, no token, no contact data."""

    state: str
    revision_number: int
    total_paise: int
    currency: str
    expires_at: datetime
    acceptance: AcceptanceStatus


class QuoteAcceptIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    revision_number: Annotated[int, Field(ge=1)]


class QuoteAcceptedOut(BaseModel):
    revision_number: int
    total_paise: int
    currency: str
    accepted_at: datetime


def _limiter(request: Request) -> RateLimitStore:
    limiter: RateLimitStore = request.app.state.quote_link_limiter
    return limiter


def _allow(request: Request) -> None:
    """Apply the public per-source limit before touching a token."""

    source = client_ip(request) or "unknown"
    try:
        # Resolve the key provider per call: a process may install it after
        # import (tests do), and a missing key must close the route, not open it.
        digest = ContactFingerprinter().keyed_digest(_RATE_LIMIT_PURPOSE, source.encode("utf-8"))
        allowed = _limiter(request).allow(digest)
    except Exception as exc:  # a limiter that cannot answer must not open
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="temporarily_unavailable",
        ) from exc
    if not allowed:
        raise HTTPException(status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail="rate_limited")


def _presentation_path(request: Request) -> Path | str:
    return getattr(request.app.state, "quote_presentation_path", DEFAULT_PRESENTATION_PATH)


def _now(request: Request) -> datetime:
    clock = getattr(request.app.state, "quote_clock", None)
    return clock() if clock is not None else datetime.now(UTC)


def _not_found() -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not_found")


@router.get("/{token}", response_model=QuoteLinkOut)
def read_quote_link(
    token: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> QuoteLinkOut:
    """Open a link: record the first view and report what is possible."""

    _allow(request)
    service = QuoteAcceptanceService(db)
    try:
        view = service.open(token, now=_now(request))
    except QuoteLinkNotFoundError as exc:
        raise _not_found() from exc
    db.commit()
    return QuoteLinkOut(
        state=view.state.value,
        revision_number=view.revision_number,
        total_paise=view.total.paise,
        currency=view.total.currency,
        expires_at=view.expires_at,
        acceptance=view.acceptance,
    )


@router.get("/{token}/document", response_class=HTMLResponse)
def read_quote_document(
    token: str,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> HTMLResponse:
    """Render the customer document behind a link, failing closed."""

    _allow(request)
    service = QuoteAcceptanceService(db)
    try:
        view = service.open(token, now=_now(request))
    except QuoteLinkNotFoundError as exc:
        raise _not_found() from exc
    if view.acceptance is AcceptanceStatus.EXPIRED:
        db.commit()
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="expired")

    quote = db.get(QuoteRecord, view.quote_id)
    if quote is None:  # pragma: no cover - a resolved link always has its row
        raise _not_found()
    try:
        rendered = QuoteRenderer(
            db,
            tenant_id=quote.tenant_id,
            presentation_path=_presentation_path(request),
        ).render_quote(quote.id, now=_now(request))
    except QuotePresentationError as exc:
        db.commit()
        # Do not publish the missing owner field names to an anonymous caller.
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail="needs_config"
        ) from exc
    db.commit()
    return HTMLResponse(content=rendered.html)


@router.post("/{token}/accept", response_model=QuoteAcceptedOut)
def accept_quote_link(
    token: str,
    payload: QuoteAcceptIn,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
) -> QuoteAcceptedOut:
    """Accept one exact revision, or refuse with a stable reason."""

    _allow(request)
    service = QuoteAcceptanceService(db)
    try:
        accepted = service.accept(
            token,
            revision_number=payload.revision_number,
            now=_now(request),
        )
    except QuoteLinkNotFoundError as exc:
        raise _not_found() from exc
    except QuoteAcceptanceError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=exc.reason) from exc
    db.commit()
    return QuoteAcceptedOut(
        revision_number=accepted.revision_number,
        total_paise=accepted.total.paise,
        currency=accepted.total.currency,
        accepted_at=accepted.accepted_at,
    )


__all__ = [
    "QuoteAcceptIn",
    "QuoteAcceptedOut",
    "QuoteLinkOut",
    "router",
]
