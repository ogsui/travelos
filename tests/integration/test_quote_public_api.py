"""Public quote link endpoints (task 016c2b).

The token is the only credential: these routes carry no staff session. The tests
prove that a valid link returns the customer-safe status and document, that an
unknown or malformed token is a plain ``404``, that an incomplete owner config
or an expired quote refuses the document, that acceptance names one exact
revision and is single, and that the per-source limit is enforced.

Every quote, token, brand, price and name here is a synthetic fixture.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from quote_fixtures import NOW, make_quote_for, move_to, write_presentation_config
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from travelos.api.app import create_app
from travelos.channels.web_form import InMemoryWindowLimiter
from travelos.core.audit import Actor
from travelos.core.config import Settings
from travelos.core.models import AuditLog
from travelos.quotes.acceptance import QuoteAcceptanceService
from travelos.quotes.events import QuoteEventStore
from travelos.quotes.models import QuoteEventKind, QuoteState
from travelos.quotes.records import QuoteRecord

pytestmark = pytest.mark.integration


@dataclass(frozen=True, slots=True)
class SeededLink:
    """A stored quote, its plaintext token, expiry and stored digest."""

    token: str
    quote_id: UUID
    expires_at: datetime
    token_digest: str


def seed_link(factory: sessionmaker[Session], *, state: QuoteState = QuoteState.SENT) -> SeededLink:
    """Create a quote with an accept link and return the plaintext token."""

    with factory() as db:
        quote = make_quote_for(db, None)
        move_to(db, quote, state)
        token = (
            QuoteAcceptanceService(db).issue_token(quote.id, actor=Actor.system(), now=NOW).token
        )
        db.commit()
        stored = db.get(QuoteRecord, quote.id)
        assert stored is not None
        return SeededLink(
            token=token,
            quote_id=stored.id,
            expires_at=stored.expires_at,
            token_digest=stored.accept_token_hash or "",
        )


@pytest.fixture
def app(database_url: str, test_settings: Settings) -> FastAPI:
    application = create_app(test_settings.model_copy(update={"database_url": database_url}))
    application.state.quote_clock = lambda: NOW
    return application


@pytest.fixture
def public_client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as client:
        yield client


# ---------------------------------------------------------------------------
# Reading a link
# ---------------------------------------------------------------------------


def test_opening_a_link_returns_the_customer_status(
    public_client: TestClient, db_factory: sessionmaker[Session]
) -> None:
    link = seed_link(db_factory)

    response = public_client.get(f"/public/quotes/{link.token}")

    assert response.status_code == 200, response.text
    body = response.json()
    assert set(body) == {
        "state",
        "revision_number",
        "total_paise",
        "currency",
        "expires_at",
        "acceptance",
    }
    assert body["state"] == "VIEWED"
    assert body["revision_number"] == 1
    assert body["currency"] == "INR"
    assert body["acceptance"] == "eligible"
    assert link.token not in response.text

    with db_factory() as db:
        events = QuoteEventStore(db).timeline(link.quote_id)
        assert [event.kind for event in events] == [QuoteEventKind.VIEWED]
        audit = db.execute(select(AuditLog).where(AuditLog.action == "quote.viewed")).scalar_one()
        assert audit.after is not None
        assert "token" not in audit.after


def test_an_unknown_token_is_not_found(public_client: TestClient) -> None:
    response = public_client.get(f"/public/quotes/{uuid4().hex}")

    assert response.status_code == 404
    assert response.json() == {"error": "not_found"}


def test_a_malformed_token_is_not_found(public_client: TestClient) -> None:
    response = public_client.get("/public/quotes/short")

    assert response.status_code == 404


# ---------------------------------------------------------------------------
# The document
# ---------------------------------------------------------------------------


def test_the_document_is_rendered_from_the_owner_config(
    app: FastAPI,
    public_client: TestClient,
    tmp_path: Path,
    db_factory: sessionmaker[Session],
) -> None:
    app.state.quote_presentation_path = write_presentation_config(tmp_path)
    link = seed_link(db_factory)

    response = public_client.get(f"/public/quotes/{link.token}/document")

    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/html")
    assert "Synthetic Travel" in response.text
    assert "Synthetic terms for tests only." in response.text
    assert "Margin" not in response.text
    assert link.token not in response.text


def test_the_document_refuses_an_incomplete_owner_config(
    public_client: TestClient, db_factory: sessionmaker[Session]
) -> None:
    link = seed_link(db_factory)

    response = public_client.get(f"/public/quotes/{link.token}/document")

    assert response.status_code == 503
    assert response.json() == {"error": "needs_config"}
    assert "display_name" not in response.text


def test_the_document_refuses_an_expired_quote(
    app: FastAPI, tmp_path: Path, db_factory: sessionmaker[Session]
) -> None:
    app.state.quote_presentation_path = write_presentation_config(tmp_path)
    link = seed_link(db_factory)

    with TestClient(app) as client:
        app.state.quote_clock = lambda: link.expires_at + timedelta(minutes=1)
        response = client.get(f"/public/quotes/{link.token}/document")

    assert response.status_code == 409
    assert response.json() == {"error": "expired"}


# ---------------------------------------------------------------------------
# Acceptance
# ---------------------------------------------------------------------------


def test_accepting_the_current_revision_records_it(
    public_client: TestClient, db_factory: sessionmaker[Session]
) -> None:
    link = seed_link(db_factory)

    response = public_client.post(
        f"/public/quotes/{link.token}/accept", json={"revision_number": 1}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["revision_number"] == 1
    assert body["currency"] == "INR"
    assert body["total_paise"] > 0
    assert link.token not in response.text

    with db_factory() as db:
        stored = db.get(QuoteRecord, link.quote_id)
        assert stored is not None
        assert stored.state == QuoteState.ACCEPTED.value
        events = QuoteEventStore(db).timeline(link.quote_id)
        assert [event.kind for event in events] == [QuoteEventKind.ACCEPTED]
        assert events[0].actor_type == "customer"


def test_accepting_a_stale_revision_is_refused(
    public_client: TestClient, db_factory: sessionmaker[Session]
) -> None:
    link = seed_link(db_factory)

    response = public_client.post(
        f"/public/quotes/{link.token}/accept", json={"revision_number": 2}
    )

    assert response.status_code == 409
    assert response.json() == {"error": "stale_revision"}


def test_accepting_twice_is_refused(
    public_client: TestClient, db_factory: sessionmaker[Session]
) -> None:
    link = seed_link(db_factory)

    first = public_client.post(f"/public/quotes/{link.token}/accept", json={"revision_number": 1})
    second = public_client.post(f"/public/quotes/{link.token}/accept", json={"revision_number": 1})

    assert first.status_code == 200
    assert second.status_code == 409
    assert second.json() == {"error": "already_accepted"}


def test_accepting_an_expired_link_is_refused(
    app: FastAPI, db_factory: sessionmaker[Session]
) -> None:
    link = seed_link(db_factory)

    with TestClient(app) as client:
        app.state.quote_clock = lambda: link.expires_at + timedelta(minutes=1)
        response = client.post(f"/public/quotes/{link.token}/accept", json={"revision_number": 1})

    assert response.status_code == 409
    assert response.json() == {"error": "expired"}


def test_a_bad_revision_number_is_rejected_before_the_service(
    public_client: TestClient, db_factory: sessionmaker[Session]
) -> None:
    link = seed_link(db_factory)

    response = public_client.post(
        f"/public/quotes/{link.token}/accept", json={"revision_number": 0}
    )

    assert response.status_code == 422


# ---------------------------------------------------------------------------
# Abuse limits and privacy
# ---------------------------------------------------------------------------


def test_each_source_is_rate_limited(
    app: FastAPI, public_client: TestClient, db_factory: sessionmaker[Session]
) -> None:
    app.state.quote_link_limiter = InMemoryWindowLimiter(
        limit=1, window_seconds=600, clock=lambda: 1_000.0
    )
    link = seed_link(db_factory)

    first = public_client.get(f"/public/quotes/{link.token}")
    second = public_client.get(f"/public/quotes/{link.token}")

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json() == {"error": "rate_limited"}


def test_no_response_carries_the_token_or_its_digest(
    public_client: TestClient, db_factory: sessionmaker[Session]
) -> None:
    link = seed_link(db_factory)

    status = public_client.get(f"/public/quotes/{link.token}")
    accepted = public_client.post(
        f"/public/quotes/{link.token}/accept", json={"revision_number": 1}
    )

    assert link.token_digest
    for response in (status, accepted):
        assert link.token not in response.text
        assert link.token_digest not in response.text
        assert "accept_token_hash" not in response.text
