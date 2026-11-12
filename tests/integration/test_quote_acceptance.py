"""Accept links, views and customer acceptance (task 016c1).

The customer link carries a random token; only its sha256 is stored, so a leaked
database row cannot be used to open a quote. Opening a link moves a SENT quote to
VIEWED once; accepting requires the exact current revision, a sent quote and an
open validity window, and is recorded as an append-only ``quote_events`` row plus
an audit entry. Tokens, hashes and customer contact data never appear in either.

Every price, tax rate, name and token here is a synthetic engineering fixture.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from uuid import uuid4

import pytest
from quote_fixtures import NOW, make_quote_for, move_to
from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.core.audit import Actor
from travelos.core.errors import AppendOnlyError
from travelos.core.models import AuditLog
from travelos.quotes.acceptance import (
    QuoteAcceptanceError,
    QuoteAcceptanceService,
    QuoteLinkNotFoundError,
)
from travelos.quotes.errors import QuoteStateError
from travelos.quotes.events import QuoteEventStore
from travelos.quotes.models import AcceptanceStatus, QuoteEventKind, QuoteState
from travelos.quotes.records import QuoteEventRecord, QuoteRecord
from travelos.quotes.store import QuoteStore

pytestmark = pytest.mark.integration

ACTOR = Actor.system()


def issue_for(
    db: Session,
    quote: QuoteRecord,
    *,
    state: QuoteState = QuoteState.SENT,
    now=NOW,
):
    move_to(db, quote, state)
    return QuoteAcceptanceService(db).issue_token(quote.id, actor=ACTOR, now=now)


# ---------------------------------------------------------------------------
# Issuing and rotating the link
# ---------------------------------------------------------------------------


def test_a_token_is_stored_only_as_a_sha256(db: Session) -> None:
    quote = make_quote_for(db, None)

    issued = issue_for(db, quote, state=QuoteState.APPROVED)

    stored = db.execute(
        select(QuoteRecord.accept_token_hash).where(QuoteRecord.id == quote.id)
    ).scalar_one()
    assert stored == hashlib.sha256(issued.token.encode("utf-8")).hexdigest()
    assert stored != issued.token
    assert issued.quote_id == quote.id
    assert issued.expires_at == quote.expires_at
    assert quote.state == QuoteState.APPROVED.value


def test_a_draft_quote_cannot_issue_an_accept_link(db: Session) -> None:
    quote = make_quote_for(db, None)

    with pytest.raises(QuoteStateError, match="DRAFT"):
        QuoteAcceptanceService(db).issue_token(quote.id, actor=ACTOR, now=NOW)


def test_an_expired_quote_cannot_issue_an_accept_link(db: Session) -> None:
    quote = make_quote_for(db, None)

    with pytest.raises(QuoteStateError, match="expired"):
        issue_for(db, quote, state=QuoteState.SENT, now=quote.expires_at)


def test_issuing_again_rotates_the_link_and_invalidates_the_old_one(db: Session) -> None:
    quote = make_quote_for(db, None)
    service = QuoteAcceptanceService(db)
    move_to(db, quote, QuoteState.SENT)
    first = service.issue_token(quote.id, actor=ACTOR, now=NOW)
    second = service.issue_token(quote.id, actor=ACTOR, now=NOW)

    assert first.token != second.token
    with pytest.raises(QuoteLinkNotFoundError):
        service.open(first.token, now=NOW)
    assert service.open(second.token, now=NOW).quote_id == quote.id

    rows = db.execute(
        select(AuditLog).where(AuditLog.action == "quote.accept_link.issued")
    ).scalars()
    assert [row.after["replaced_previous"] for row in rows] == [False, True]


def test_an_unknown_token_is_not_found(db: Session) -> None:
    service = QuoteAcceptanceService(db)

    with pytest.raises(QuoteLinkNotFoundError):
        service.open("x" * 43, now=NOW)


# ---------------------------------------------------------------------------
# Opening the link
# ---------------------------------------------------------------------------


def test_opening_a_sent_quote_marks_it_viewed_and_audits_once(db: Session) -> None:
    quote = make_quote_for(db, None)
    issued = issue_for(db, quote)
    service = QuoteAcceptanceService(db)

    first = service.open(issued.token, now=NOW)
    second = service.open(issued.token, now=NOW)

    assert first.state is QuoteState.VIEWED
    assert first.acceptance is AcceptanceStatus.ELIGIBLE
    assert first.revision_number == 1
    assert first.total == QuoteStore(db).load(quote.id).current.total
    assert second.state is QuoteState.VIEWED

    events = QuoteEventStore(db).timeline(quote.id)
    assert [event.kind for event in events] == [QuoteEventKind.VIEWED]
    audits = list(db.execute(select(AuditLog).where(AuditLog.action == "quote.viewed")).scalars())
    assert len(audits) == 1
    assert audits[0].after is not None
    assert audits[0].after["revision_number"] == 1
    assert "token" not in audits[0].after


def test_an_expired_link_still_opens_but_reports_expiry(db: Session) -> None:
    quote = make_quote_for(db, None)
    issued = issue_for(db, quote)

    view = QuoteAcceptanceService(db).open(
        issued.token, now=quote.expires_at + timedelta(minutes=1)
    )

    assert view.acceptance is AcceptanceStatus.EXPIRED
    assert view.state is QuoteState.SENT


# ---------------------------------------------------------------------------
# Accepting
# ---------------------------------------------------------------------------


def test_accepting_the_current_revision_records_it_without_the_token(db: Session) -> None:
    quote = make_quote_for(db, None)
    issued = issue_for(db, quote)
    service = QuoteAcceptanceService(db)
    service.open(issued.token, now=NOW)
    revision = QuoteStore(db).load(quote.id).current

    accepted = service.accept(issued.token, revision_number=revision.number, now=NOW)

    assert accepted.quote_id == quote.id
    assert accepted.revision_number == revision.number
    assert accepted.total == revision.total
    assert accepted.accepted_at == NOW
    assert quote.state == QuoteState.ACCEPTED.value

    events = QuoteEventStore(db).timeline(quote.id)
    assert [event.kind for event in events] == [
        QuoteEventKind.VIEWED,
        QuoteEventKind.ACCEPTED,
    ]
    assert events[-1].total == revision.total
    assert events[-1].actor_type == "customer"

    audits = list(db.execute(select(AuditLog).where(AuditLog.action == "quote.accepted")).scalars())
    assert len(audits) == 1
    assert audits[0].after is not None
    assert audits[0].after["revision_number"] == revision.number
    assert audits[0].after["total_paise"] == revision.total.paise
    assert "token" not in audits[0].after


def test_accepting_a_stale_revision_is_refused(db: Session) -> None:
    quote = make_quote_for(db, None)
    issued = issue_for(db, quote)

    with pytest.raises(QuoteAcceptanceError, match="stale_revision"):
        QuoteAcceptanceService(db).accept(
            issued.token, revision_number=quote.current_revision + 1, now=NOW
        )

    assert quote.state == QuoteState.SENT.value
    assert QuoteEventStore(db).timeline(quote.id) == ()


def test_an_expired_quote_cannot_be_accepted(db: Session) -> None:
    quote = make_quote_for(db, None)
    issued = issue_for(db, quote)

    with pytest.raises(QuoteAcceptanceError, match="expired"):
        QuoteAcceptanceService(db).accept(
            issued.token,
            revision_number=quote.current_revision,
            now=quote.expires_at + timedelta(minutes=1),
        )

    assert quote.state == QuoteState.SENT.value


def test_a_quote_that_was_not_sent_cannot_be_accepted(db: Session) -> None:
    quote = make_quote_for(db, None)
    issued = issue_for(db, quote, state=QuoteState.APPROVED)

    with pytest.raises(QuoteAcceptanceError, match="not_sent"):
        QuoteAcceptanceService(db).accept(
            issued.token, revision_number=quote.current_revision, now=NOW
        )


def test_a_second_acceptance_is_refused_and_records_nothing_new(db: Session) -> None:
    quote = make_quote_for(db, None)
    issued = issue_for(db, quote)
    service = QuoteAcceptanceService(db)
    service.accept(issued.token, revision_number=quote.current_revision, now=NOW)

    with pytest.raises(QuoteAcceptanceError, match="already_accepted"):
        service.accept(issued.token, revision_number=quote.current_revision, now=NOW)

    events = QuoteEventStore(db).timeline(quote.id)
    assert [event.kind for event in events] == [QuoteEventKind.ACCEPTED]


def test_a_public_link_is_resolved_without_the_staff_tenant(db: Session) -> None:
    quote = make_quote_for(db, None)
    issued = issue_for(db, quote)

    view = QuoteAcceptanceService(db, tenant_id=uuid4()).open(issued.token, now=NOW)

    assert view.quote_id == quote.id


# ---------------------------------------------------------------------------
# Event log
# ---------------------------------------------------------------------------


def test_quote_events_are_append_only(db: Session) -> None:
    quote = make_quote_for(db, None)
    issued = issue_for(db, quote)
    QuoteAcceptanceService(db).open(issued.token, now=NOW)
    event = db.execute(select(QuoteEventRecord)).scalars().one()

    event.kind = QuoteEventKind.ACCEPTED.value

    with pytest.raises(AppendOnlyError, match="append-only"):
        db.flush()
