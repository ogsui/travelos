"""Owner price overrides for below-floor or uncompetitive prices (task 016c2c).

The approval matrix keeps ``quote.below_floor`` a LOCKED A0 action, so the owner's
decision is an append-only override row, not an approval request. The tests pin the
contract: only an active owner may record it, only after the routing step created
the decision task, the owner total becomes a deterministic calculation, and a quote
revision can only cite a non-``ok`` calculation with the matching override.

Every price, margin and tax value here is a synthetic engineering fixture.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from quote_fixtures import NOW, make_itinerary, make_user, record_price
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from travelos.core.audit import Actor
from travelos.core.errors import AppendOnlyError
from travelos.core.models import AuditLog, HumanTask, User
from travelos.core.money import money
from travelos.pricing.records import PriceCalculationRecord
from travelos.pricing.routing import route_to_owner
from travelos.quotes.errors import (
    QuotePriceOverrideError,
    QuotePriceOverrideNotFoundError,
    QuotePricingError,
    QuoteValidationError,
)
from travelos.quotes.overrides import QuotePriceOverrideService
from travelos.quotes.records import QuotePriceOverrideRecord, QuoteRevisionRecord
from travelos.quotes.store import QuoteStore

pytestmark = pytest.mark.integration

DECIDED_AT = NOW + timedelta(hours=2)


def below_floor(db: Session) -> tuple[PriceCalculationRecord, User, HumanTask]:
    """A synthetic below-floor calculation with the routing task it produced."""

    owner = make_user(db)
    calculation, result = record_price(db, proposed=money(100_000))
    routing = route_to_owner(db, result=result, calculation_id=calculation.id)
    assert routing is not None
    task = db.get(HumanTask, routing.task_id)
    assert task is not None
    return calculation, owner, task


def uncompetitive(db: Session) -> tuple[PriceCalculationRecord, User, HumanTask]:
    """An uncompetitive calculation: the trusted ceiling is below the floor."""

    owner = make_user(db)
    calculation, result = record_price(db, competitor=money(110_000))
    routing = route_to_owner(db, result=result, calculation_id=calculation.id)
    assert routing is not None
    task = db.get(HumanTask, routing.task_id)
    assert task is not None
    return calculation, owner, task


def agent(db: Session) -> User:
    user = User(
        email=f"agent-{uuid4().hex[:8]}@example.test",
        password_hash="x" * 20,
        role="agent",
    )
    db.add(user)
    db.flush()
    return user


def record(
    db: Session,
    calculation: PriceCalculationRecord,
    owner: User,
    *,
    total: int = 102_000,
    reason: str = "Synthetic owner decision for tests.",
) -> QuotePriceOverrideRecord:
    return QuotePriceOverrideService(db).record(
        calculation.id,
        owner_total=money(total),
        reason=reason,
        actor=Actor.user(owner.id),
        now=DECIDED_AT,
    )


def audit_rows(db: Session, action: str) -> list[AuditLog]:
    return list(db.execute(select(AuditLog).where(AuditLog.action == action)).scalars())


# ---------------------------------------------------------------------------
# A recorded decision
# ---------------------------------------------------------------------------


def test_the_owner_can_authorise_a_below_floor_price_at_a_different_total(
    db: Session,
) -> None:
    source, owner, task = below_floor(db)
    assert source.customer_price_paise == 100_000

    override = record(db, source, owner, total=102_000)

    assert override.source_calculation_id == source.id
    assert override.owner_user_id == owner.id
    assert override.decision_task_id == task.id
    assert override.status == "below_floor"
    assert override.owner_total_paise == 102_000
    assert override.source_customer_price_paise == 100_000
    assert override.floor_paise == 105_250
    assert override.decided_at == DECIDED_AT
    assert override.currency == "INR"
    assert db.get(HumanTask, task.id).status == "done"

    authorised = db.get(PriceCalculationRecord, override.authorised_calculation_id)
    assert authorised is not None
    assert authorised.id != source.id
    assert authorised.status == "below_floor"
    assert authorised.customer_price_paise == 102_000
    assert authorised.quote_revision_id is None
    outputs = authorised.outputs
    lines = outputs["lines"]
    assert sum(line["amount"]["paise"] for line in lines) == 102_000
    assert lines[-1]["kind"] == "proposal"
    # The override never extends the price validity.
    assert outputs["expires_at"] == source.outputs["expires_at"]
    # No reviewer identity or contact detail leaks into the calculation snapshot.
    assert owner.email not in str(outputs)

    entries = audit_rows(db, "quote.price_override.recorded")
    assert len(entries) == 1
    after = entries[0].after
    assert after is not None
    assert after["source_calculation_id"] == str(source.id)
    assert after["owner_total_paise"] == 102_000
    assert after["reason_length"] == len(override.reason)
    assert "reason" not in after
    completed = audit_rows(db, "pricing.owner_decision.completed")
    assert len(completed) == 1
    assert completed[0].subject_id == source.id


def test_the_authorised_calculation_becomes_a_quote_revision(db: Session) -> None:
    source, owner, _ = below_floor(db)
    override = record(db, source, owner)
    itinerary_id = make_itinerary(db)

    quote = QuoteStore(db).create(
        calculation_id=override.authorised_calculation_id,
        itinerary_id=itinerary_id,
        override_id=override.id,
    )

    assert quote.state == "DRAFT"
    view = QuoteStore(db).load(quote.id)
    assert view.current.total == money(102_000)
    assert sum(line.amount.paise for line in view.current.lines) == 102_000

    revision_row = (
        db.execute(select(QuoteRevisionRecord).where(QuoteRevisionRecord.quote_id == quote.id))
        .scalars()
        .one()
    )
    assert revision_row.pricing_override_id == override.id
    assert revision_row.price_calculation_id == override.authorised_calculation_id

    db.flush()
    created = audit_rows(db, "quote.created")
    assert created[0].after is not None
    assert created[0].after["pricing_override_id"] == str(override.id)


def test_an_uncompetitive_price_can_be_authorised(db: Session) -> None:
    source, owner, _ = uncompetitive(db)
    assert source.status == "uncompetitive"

    override = record(db, source, owner, total=104_000)

    assert override.status == "uncompetitive"
    assert override.owner_total_paise == 104_000
    authorised = db.get(PriceCalculationRecord, override.authorised_calculation_id)
    assert authorised is not None
    assert authorised.customer_price_paise == 104_000


def test_the_owner_total_lands_in_the_bounds_when_it_is_high_enough(db: Session) -> None:
    source, owner, _ = below_floor(db)
    override = record(db, source, owner, total=110_000)

    authorised = db.get(PriceCalculationRecord, override.authorised_calculation_id)
    assert authorised is not None
    assert authorised.status == "ok"
    assert authorised.customer_price_paise == 110_000


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


def test_only_an_active_owner_may_record_an_override(db: Session) -> None:
    source, _, _ = below_floor(db)
    staff = agent(db)

    with pytest.raises(QuotePriceOverrideError, match="only the owner"):
        QuotePriceOverrideService(db).record(
            source.id,
            owner_total=money(102_000),
            reason="Synthetic owner decision for tests.",
            actor=Actor.user(staff.id),
            now=DECIDED_AT,
        )

    with pytest.raises(QuotePriceOverrideError, match="signed-in owner"):
        QuotePriceOverrideService(db).record(
            source.id,
            owner_total=money(102_000),
            reason="Synthetic owner decision for tests.",
            actor=Actor.system(),
            now=DECIDED_AT,
        )


def test_an_ok_calculation_needs_no_override(db: Session) -> None:
    owner = make_user(db)
    calculation, result = record_price(db)
    assert result.status.value == "ok"

    with pytest.raises(QuotePriceOverrideError, match="only a below_floor or uncompetitive"):
        record(db, calculation, owner)


def test_the_override_needs_the_routing_task(db: Session) -> None:
    owner = make_user(db)
    calculation, _ = record_price(db, proposed=money(100_000))

    with pytest.raises(QuotePriceOverrideError, match="no owner decision task"):
        record(db, calculation, owner)


def test_a_decision_can_be_recorded_only_once(db: Session) -> None:
    source, owner, task = below_floor(db)
    record(db, source, owner)

    with pytest.raises(QuotePriceOverrideError, match="only be recorded once"):
        record(db, source, owner, total=103_000)

    assert db.get(HumanTask, task.id).status == "done"


def test_the_reason_must_be_substantial(db: Session) -> None:
    source, owner, _ = below_floor(db)

    with pytest.raises(QuotePriceOverrideError, match="at least 5 characters"):
        record(db, source, owner, reason="   ")

    with pytest.raises(QuotePriceOverrideError, match="at most 2000 characters"):
        record(db, source, owner, reason="x" * 2_001)


def test_the_owner_total_must_sit_on_the_rounding_step(db: Session) -> None:
    source, owner, _ = below_floor(db)

    with pytest.raises(QuotePriceOverrideError, match="multiple of 100 paise"):
        record(db, source, owner, total=102_050)


def test_a_negative_owner_total_is_refused(db: Session) -> None:
    source, owner, _ = below_floor(db)

    with pytest.raises(QuotePriceOverrideError, match="negative"):
        record(db, source, owner, total=-100)


def test_an_unknown_calculation_is_refused(db: Session) -> None:
    owner = make_user(db)

    with pytest.raises(QuotePriceOverrideError, match="was not found"):
        QuotePriceOverrideService(db).record(
            uuid4(),
            owner_total=money(102_000),
            reason="Synthetic owner decision for tests.",
            actor=Actor.user(owner.id),
            now=DECIDED_AT,
        )


def test_another_tenant_cannot_read_the_override(db: Session) -> None:
    source, owner, _ = below_floor(db)
    override = record(db, source, owner)

    with pytest.raises(QuotePriceOverrideNotFoundError, match="was not found"):
        QuotePriceOverrideService(db, tenant_id=uuid4()).get(override.id)


# ---------------------------------------------------------------------------
# The quote store still fails closed
# ---------------------------------------------------------------------------


def test_a_below_floor_calculation_still_needs_an_override(db: Session) -> None:
    source, _, _ = below_floor(db)
    itinerary_id = make_itinerary(db)

    with pytest.raises(QuotePricingError, match="owner price override is required"):
        QuoteStore(db).create(calculation_id=source.id, itinerary_id=itinerary_id)


def test_an_override_only_authorises_the_calculation_it_cites(db: Session) -> None:
    source, owner, _ = below_floor(db)
    override = record(db, source, owner)
    other, _ = record_price(db)
    itinerary_id = make_itinerary(db)

    with pytest.raises(QuoteValidationError, match="authorised calculation"):
        QuoteStore(db).create(
            calculation_id=other.id,
            itinerary_id=itinerary_id,
            override_id=override.id,
        )


def test_an_override_backs_only_one_revision(db: Session) -> None:
    source, owner, _ = below_floor(db)
    override = record(db, source, owner)
    itinerary_id = make_itinerary(db)
    QuoteStore(db).create(
        calculation_id=override.authorised_calculation_id,
        itinerary_id=itinerary_id,
        override_id=override.id,
    )

    with pytest.raises(QuoteValidationError, match="already backs revision"):
        QuoteStore(db).create(
            calculation_id=override.authorised_calculation_id,
            itinerary_id=make_itinerary(db),
            override_id=override.id,
        )


def test_the_override_row_is_append_only(db: Session) -> None:
    source, owner, _ = below_floor(db)
    override = record(db, source, owner)

    override.reason = "changed"
    with pytest.raises(AppendOnlyError, match="UPDATE is forbidden"):
        db.flush()
    db.rollback()


def test_the_database_also_blocks_a_raw_override_update(db: Session) -> None:
    source, owner, _ = below_floor(db)
    override = record(db, source, owner)

    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(
            text("UPDATE quote_price_overrides SET reason = 'tampered' WHERE id = :id"),
            {"id": override.id},
        )
    db.rollback()
