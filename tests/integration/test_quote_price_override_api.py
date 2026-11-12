"""The owner-only price override endpoint (task 016c2c2).

The route is the HTTP face of ``QuotePriceOverrideService``: the permission is
owner-only and the service repeats the check against the user row, so no other
role can decide a price even if the role matrix were wrong. The tests pin the
success shape, the fail-closed refusals and the audit trail.

Every calculation, price, reason and identity here is a synthetic fixture.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import datetime, timedelta
from uuid import UUID, uuid4

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from quote_fixtures import NOW, record_price
from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from travelos.api.app import create_app
from travelos.core.config import Settings
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.models import AuditLog, HumanTask
from travelos.core.money import money
from travelos.pricing.records import PriceCalculationRecord
from travelos.pricing.routing import route_to_owner
from travelos.quotes.records import QuotePriceOverrideRecord
from travelos.security.totp import generate_code

pytestmark = pytest.mark.integration

DECIDED_AT = NOW + timedelta(hours=2)
REASON = "Synthetic owner decision for tests."
URL = "/quotes/price-overrides"


@pytest.fixture
def app(database_url: str, test_settings: Settings) -> FastAPI:
    application = create_app(test_settings.model_copy(update={"database_url": database_url}))
    application.state.quote_clock = lambda: DECIDED_AT
    return application


@pytest.fixture
def client(app: FastAPI) -> Iterator[TestClient]:
    with TestClient(app) as app_client:
        yield app_client


def seed_below_floor(
    factory: sessionmaker[Session],
    *,
    tenant_id: UUID | None = None,
    route: bool = True,
) -> tuple[UUID, UUID | None]:
    """A below-floor calculation and, unless skipped, its decision task."""

    with factory() as db:
        calculation, result = record_price(
            db,
            proposed=money(100_000),
            tenant_id=tenant_id or DEFAULT_TENANT_ID,
        )
        task_id: UUID | None = None
        if route:
            routing = route_to_owner(
                db,
                result=result,
                calculation_id=calculation.id,
                tenant_id=tenant_id or DEFAULT_TENANT_ID,
            )
            assert routing is not None
            task_id = routing.task_id
        calculation_id = calculation.id
        db.commit()
        return calculation_id, task_id


def owner_token(client: TestClient, make_user, login) -> str:
    user = make_user(email="owner@example.test", role="owner", mfa_enrolled=True)
    response = login(client, user, code=generate_code(user.mfa_secret))
    assert response.status_code == 200, response.text
    return str(response.json()["access_token"])


def post_override(
    client: TestClient,
    token: str,
    calculation_id: UUID,
    *,
    total: int = 102_000,
    reason: str = REASON,
    task_id: UUID | None = None,
):
    payload: dict[str, object] = {
        "calculation_id": str(calculation_id),
        "owner_total_paise": total,
        "reason": reason,
    }
    if task_id is not None:
        payload["decision_task_id"] = str(task_id)
    return client.post(URL, json=payload, headers={"Authorization": f"Bearer {token}"})


def stored_override(factory: sessionmaker[Session], override_id: UUID) -> QuotePriceOverrideRecord:
    with factory() as db:
        row = db.get(QuotePriceOverrideRecord, override_id)
        assert row is not None
        return row


# ---------------------------------------------------------------------------
# The recorded decision
# ---------------------------------------------------------------------------


def test_the_owner_records_an_override_over_http(
    client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    calculation_id, task_id = seed_below_floor(db_factory)
    token = owner_token(client, make_user, login)

    response = post_override(client, token, calculation_id, task_id=task_id)

    assert response.status_code == 201, response.text
    body = response.json()
    assert set(body) == {
        "override_id",
        "source_calculation_id",
        "authorised_calculation_id",
        "decision_task_id",
        "status",
        "owner_total_paise",
        "currency",
        "decided_at",
    }
    assert body["source_calculation_id"] == str(calculation_id)
    assert body["authorised_calculation_id"] != str(calculation_id)
    assert body["decision_task_id"] == str(task_id)
    assert body["status"] == "below_floor"
    assert body["owner_total_paise"] == 102_000
    assert body["currency"] == "INR"
    assert datetime.fromisoformat(body["decided_at"].replace("Z", "+00:00")) == DECIDED_AT
    # The reason is stored, not echoed; no owner identity or cost leaks back.
    assert REASON not in response.text
    assert "owner@example.test" not in response.text

    with db_factory() as db:
        override = db.get(QuotePriceOverrideRecord, UUID(body["override_id"]))
        assert override is not None
        assert override.source_calculation_id == calculation_id
        assert override.decision_task_id == task_id
        assert override.owner_total_paise == 102_000
        assert override.reason == REASON
        task = db.get(HumanTask, task_id)
        assert task is not None and task.status == "done"

        source = db.get(PriceCalculationRecord, calculation_id)
        authorised = db.get(PriceCalculationRecord, override.authorised_calculation_id)
        assert source is not None and authorised is not None
        assert authorised.customer_price_paise == 102_000
        # An override never extends how long the price stays valid.
        assert authorised.outputs["expires_at"] == source.outputs["expires_at"]

        actions = [
            row.action for row in db.execute(select(AuditLog).order_by(AuditLog.at)).scalars()
        ]
        assert actions.count("quote.price_override.recorded") == 1
        assert actions.count("pricing.owner_decision.completed") == 1


def test_the_owner_may_leave_the_task_for_the_service_to_find(
    client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    calculation_id, task_id = seed_below_floor(db_factory)
    token = owner_token(client, make_user, login)

    response = post_override(client, token, calculation_id)

    assert response.status_code == 201, response.text
    assert response.json()["decision_task_id"] == str(task_id)


def test_the_owner_can_raise_the_total_into_the_bounds(
    client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    calculation_id, _ = seed_below_floor(db_factory)
    token = owner_token(client, make_user, login)

    response = post_override(client, token, calculation_id, total=110_000)

    assert response.status_code == 201, response.text
    with db_factory() as db:
        authorised = db.get(
            PriceCalculationRecord,
            UUID(response.json()["authorised_calculation_id"]),
        )
        assert authorised is not None
        assert authorised.status == "ok"
        assert authorised.customer_price_paise == 110_000


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_an_unknown_calculation_is_not_found(
    client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    token = owner_token(client, make_user, login)

    response = post_override(client, token, uuid4())

    assert response.status_code == 404
    assert response.json() == {"error": "not_found"}
    with db_factory() as db:
        assert db.execute(select(QuotePriceOverrideRecord)).scalars().first() is None


def test_another_tenants_calculation_is_not_found(
    client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    calculation_id, _ = seed_below_floor(db_factory, tenant_id=uuid4())
    token = owner_token(client, make_user, login)

    response = post_override(client, token, calculation_id)

    assert response.status_code == 404
    assert response.json() == {"error": "not_found"}


def test_an_ok_calculation_needs_no_override(
    client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    with db_factory() as db:
        calculation, _ = record_price(db)
        calculation_id = calculation.id
        db.commit()
    token = owner_token(client, make_user, login)

    response = post_override(client, token, calculation_id)

    assert response.status_code == 409
    assert "only a below_floor or uncompetitive" in response.json()["error"]


def test_a_calculation_without_the_routing_task_is_refused(
    client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    calculation_id, _ = seed_below_floor(db_factory, route=False)
    token = owner_token(client, make_user, login)

    response = post_override(client, token, calculation_id)

    assert response.status_code == 409
    assert "no owner decision task" in response.json()["error"]


def test_naming_another_calculations_task_is_refused(
    client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    calculation_id, _ = seed_below_floor(db_factory)
    _, other_task_id = seed_below_floor(db_factory)
    token = owner_token(client, make_user, login)

    response = post_override(client, token, calculation_id, task_id=other_task_id)

    assert response.status_code == 409
    assert "no owner decision task" in response.json()["error"]


def test_a_decision_is_recorded_only_once(
    client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    calculation_id, task_id = seed_below_floor(db_factory)
    token = owner_token(client, make_user, login)
    assert post_override(client, token, calculation_id).status_code == 201

    response = post_override(client, token, calculation_id, total=103_000)

    assert response.status_code == 409
    assert "only be recorded once" in response.json()["error"]
    with db_factory() as db:
        overrides = list(db.execute(select(QuotePriceOverrideRecord)).scalars())
        assert len(overrides) == 1
        task = db.get(HumanTask, task_id)
        assert task is not None and task.status == "done"


def test_the_total_must_sit_on_the_rounding_step(
    client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    calculation_id, _ = seed_below_floor(db_factory)
    token = owner_token(client, make_user, login)

    response = post_override(client, token, calculation_id, total=102_050)

    assert response.status_code == 409
    assert "multiple of 100 paise" in response.json()["error"]


def test_a_blank_reason_is_refused(
    client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    calculation_id, _ = seed_below_floor(db_factory)
    token = owner_token(client, make_user, login)

    response = post_override(client, token, calculation_id, reason=" " * 5)

    assert response.status_code == 409
    assert "at least 5 characters" in response.json()["error"]


def test_a_negative_total_is_rejected_before_the_service(
    client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    calculation_id, _ = seed_below_floor(db_factory)
    token = owner_token(client, make_user, login)

    response = post_override(client, token, calculation_id, total=-100)

    assert response.status_code == 422


def test_an_extra_field_is_rejected(
    client: TestClient,
    make_user,
    login,
    db_factory: sessionmaker[Session],
) -> None:
    calculation_id, _ = seed_below_floor(db_factory)
    token = owner_token(client, make_user, login)

    response = client.post(
        URL,
        json={
            "calculation_id": str(calculation_id),
            "owner_total_paise": 102_000,
            "reason": REASON,
            "margin_bps": 900,
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 422
