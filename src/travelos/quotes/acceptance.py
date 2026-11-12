"""Accept links, views and acceptance for stored quotes (task 016c1).

A customer link carries a random token. Only its sha256 digest is stored on
``quotes.accept_token_hash``, so a database reader cannot reconstruct a working
link. The public side resolves the token without a staff tenant: the unguessable
token identifies the quote, and an ambiguous digest fails closed.

Acceptance is a state change, not a price decision. It requires a sent (or
viewed) quote, an open validity window and the exact current revision; anything
else is refused with a customer-safe reason. The accepted revision and total are
copied into an append-only ``quote_events`` row and an audit record. The token and
its digest are never copied into either.

The caller owns the transaction.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.core.audit import Actor, AuditWriter
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.money import Money
from travelos.quotes.errors import (
    QuoteAcceptanceError,
    QuoteLinkNotFoundError,
    QuoteNotFoundError,
    QuoteStateError,
    QuoteValidationError,
)
from travelos.quotes.events import QuoteEventStore
from travelos.quotes.models import (
    AcceptanceStatus,
    QuoteEventKind,
    QuoteState,
    acceptance_status,
)
from travelos.quotes.records import QuoteRecord, QuoteRevisionRecord

TOKEN_BYTES = 32
_TOKEN_MIN_LENGTH = 32
_TOKEN_MAX_LENGTH = 512
_ISSUABLE = frozenset({QuoteState.APPROVED, QuoteState.SENT, QuoteState.VIEWED})


def new_accept_token() -> str:
    """A fresh URL-safe bearer token; only its digest is ever stored."""

    return secrets.token_urlsafe(TOKEN_BYTES)


def token_digest(token: str) -> str:
    """The stored sha256 hex of a presented token, refusing a malformed one."""

    if not isinstance(token, str) or not _TOKEN_MIN_LENGTH <= len(token) <= _TOKEN_MAX_LENGTH:
        raise QuoteLinkNotFoundError("quote link is not valid")
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class IssuedToken:
    """A one-time disclosure of the plaintext token to the sender."""

    quote_id: UUID
    token: str
    expires_at: datetime


@dataclass(frozen=True, slots=True)
class QuoteLinkView:
    """What a link holder may see about the quote behind their token."""

    quote_id: UUID
    state: QuoteState
    revision_number: int
    total: Money
    expires_at: datetime
    acceptance: AcceptanceStatus


@dataclass(frozen=True, slots=True)
class QuoteAcceptance:
    """The recorded acceptance of one exact revision."""

    quote_id: UUID
    revision_number: int
    total: Money
    accepted_at: datetime


class QuoteAcceptanceService:
    """Issues links for staff and serves the public open/accept operations."""

    def __init__(
        self,
        db: Session,
        *,
        tenant_id: UUID = DEFAULT_TENANT_ID,
        audit: AuditWriter | None = None,
    ) -> None:
        self._db = db
        self._tenant_id = tenant_id
        self._audit = audit or AuditWriter()

    # ------------------------------------------------------------------
    # Staff side
    # ------------------------------------------------------------------

    def issue_token(
        self,
        quote_id: UUID,
        *,
        actor: Actor,
        now: datetime,
    ) -> IssuedToken:
        """Mint a new link, invalidating any previous one, and audit it."""

        instant = _aware(now, "now")
        quote = self._lock_for_tenant(quote_id)
        state = QuoteState(quote.state)
        if state not in _ISSUABLE:
            raise QuoteStateError(
                f"quote {quote_id} is {state.value}; "
                "an accept link requires APPROVED, SENT or VIEWED"
            )
        expires_at = _aware(quote.expires_at, "quote expiry")
        if instant >= expires_at:
            raise QuoteStateError(f"quote {quote_id} is expired; an accept link cannot be issued")

        replaced = quote.accept_token_hash is not None
        token = new_accept_token()
        quote.accept_token_hash = token_digest(token)
        self._db.flush()
        self._audit.record(
            self._db,
            action="quote.accept_link.issued",
            actor=actor,
            subject_type="quote",
            subject_id=quote.id,
            after={
                "state": state.value,
                "revision_number": quote.current_revision,
                "expires_at": expires_at,
                "replaced_previous": replaced,
            },
        )
        self._db.flush()
        return IssuedToken(quote_id=quote.id, token=token, expires_at=expires_at)

    # ------------------------------------------------------------------
    # Public side
    # ------------------------------------------------------------------

    def open(self, token: str, *, now: datetime) -> QuoteLinkView:
        """Resolve a link, record the first view and report what is possible."""

        instant = _aware(now, "now")
        quote = self._find(token)
        expires_at = _aware(quote.expires_at, "quote expiry")
        state = QuoteState(quote.state)

        if state is QuoteState.SENT and instant < expires_at:
            quote = self._lock(quote.id)
            if quote.state == QuoteState.SENT.value:
                quote.state = QuoteState.VIEWED.value
                self._db.flush()
                self._record(
                    quote,
                    kind=QuoteEventKind.VIEWED,
                    actor_type="customer",
                    at=instant,
                )
                self._audit.record(
                    self._db,
                    action="quote.viewed",
                    actor=Actor.system(),
                    subject_type="quote",
                    subject_id=quote.id,
                    after={
                        "state": quote.state,
                        "revision_number": quote.current_revision,
                        "via": "public_link",
                    },
                )
                self._db.flush()
                state = QuoteState.VIEWED

        return QuoteLinkView(
            quote_id=quote.id,
            state=state,
            revision_number=quote.current_revision,
            total=self._revision_total(quote),
            expires_at=expires_at,
            acceptance=acceptance_status(
                state=state,
                expires_at=expires_at,
                now=instant,
            ),
        )

    def accept(
        self,
        token: str,
        *,
        revision_number: int,
        now: datetime,
    ) -> QuoteAcceptance:
        """Accept the exact revision the customer saw, or refuse with a reason."""

        instant = _aware(now, "now")
        found = self._find(token)
        quote = self._lock(found.id)
        state = QuoteState(quote.state)
        expires_at = _aware(quote.expires_at, "quote expiry")
        status = acceptance_status(state=state, expires_at=expires_at, now=instant)

        if status is AcceptanceStatus.ALREADY_ACCEPTED:
            raise QuoteAcceptanceError(quote.id, "already_accepted")
        if status is AcceptanceStatus.EXPIRED:
            raise QuoteAcceptanceError(quote.id, "expired")
        if status is AcceptanceStatus.NOT_SENT:
            raise QuoteAcceptanceError(quote.id, "not_sent")
        if revision_number != quote.current_revision:
            raise QuoteAcceptanceError(quote.id, "stale_revision")

        total = self._revision_total(quote)
        quote.state = QuoteState.ACCEPTED.value
        self._db.flush()
        self._record(
            quote,
            kind=QuoteEventKind.ACCEPTED,
            actor_type="customer",
            at=instant,
            revision_number=revision_number,
        )
        self._audit.record(
            self._db,
            action="quote.accepted",
            actor=Actor.system(),
            subject_type="quote",
            subject_id=quote.id,
            after={
                "state": quote.state,
                "revision_number": revision_number,
                "total_paise": total.paise,
                "currency": total.currency,
                "via": "public_link",
            },
        )
        self._db.flush()
        return QuoteAcceptance(
            quote_id=quote.id,
            revision_number=revision_number,
            total=total,
            accepted_at=instant,
        )

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _find(self, token: str) -> QuoteRecord:
        digest = token_digest(token)
        rows = list(
            self._db.execute(
                select(QuoteRecord).where(QuoteRecord.accept_token_hash == digest).limit(2)
            ).scalars()
        )
        if len(rows) != 1:
            raise QuoteLinkNotFoundError("quote link is not valid")
        return rows[0]

    def _lock_for_tenant(self, quote_id: UUID) -> QuoteRecord:
        quote = self._db.execute(
            select(QuoteRecord)
            .where(
                QuoteRecord.id == quote_id,
                QuoteRecord.tenant_id == self._tenant_id,
            )
            .with_for_update()
        ).scalar_one_or_none()
        if quote is None:
            raise QuoteNotFoundError(f"quote {quote_id} was not found")
        return quote

    def _lock(self, quote_id: UUID) -> QuoteRecord:
        quote = self._db.execute(
            select(QuoteRecord).where(QuoteRecord.id == quote_id).with_for_update()
        ).scalar_one_or_none()
        if quote is None:
            raise QuoteLinkNotFoundError("quote link is not valid")
        return quote

    def _revision_total(self, quote: QuoteRecord) -> Money:
        row = self._db.execute(
            select(
                QuoteRevisionRecord.total_paise,
                QuoteRevisionRecord.currency,
            ).where(
                QuoteRevisionRecord.tenant_id == quote.tenant_id,
                QuoteRevisionRecord.quote_id == quote.id,
                QuoteRevisionRecord.number == quote.current_revision,
            )
        ).one_or_none()
        if row is None:
            raise QuoteValidationError(
                f"quote {quote.id} has no stored revision {quote.current_revision}"
            )
        return Money(row.total_paise, row.currency)

    def _record(
        self,
        quote: QuoteRecord,
        *,
        kind: QuoteEventKind,
        actor_type: str,
        at: datetime,
        revision_number: int | None = None,
    ) -> None:
        QuoteEventStore(self._db, tenant_id=quote.tenant_id).record(
            quote,
            kind=kind,
            revision_number=(
                quote.current_revision if revision_number is None else revision_number
            ),
            total=self._revision_total(quote),
            actor_type=actor_type,
            at=at,
        )


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise QuoteValidationError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


__all__ = [
    "TOKEN_BYTES",
    "IssuedToken",
    "QuoteAcceptance",
    "QuoteAcceptanceService",
    "QuoteLinkView",
    "new_accept_token",
    "token_digest",
]
