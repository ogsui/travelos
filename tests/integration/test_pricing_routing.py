"""Owner routing for below-floor and uncompetitive prices (task 015d).

The approval matrix makes both cases a LOCKED A0 decision, so the system must
prepare the data and hand it to the owner rather than open an approval request.
Every rule, tax and competitor value here is a synthetic engineering fixture.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.core.models import AuditLog, HumanTask, User
from travelos.core.money import Money, money
from travelos.market.adapters import CompetitorFetch
from travelos.market.models import ComparabilityBasis, CompetitorPrice, FetchStatus
from travelos.market.store import CompetitorSnapshotStore
from travelos.pricing.calculator import (
    ItemCost,
    PriceRequest,
    PriceResult,
    PriceStatus,
    price_quote,
)
from travelos.pricing.ceiling import trusted_ceiling
from travelos.pricing.errors import PricingValidationError
from travelos.pricing.models import (
    CompetitorPolicy,
    CustomerSegment,
    GatewayFeePolicy,
    GstBasis,
    MarginCombination,
    MarginRule,
    PricingProduct,
    PricingRuleSet,
    RevalidationTolerance,
    RoundingMode,
    RoundingRule,
    TcsPolicy,
)
from travelos.pricing.records import PriceCalculationRecord
from travelos.pricing.routing import route_to_owner
from travelos.pricing.store import PricingStore, StoredRuleSet, StoredTaxTable
from travelos.pricing.tax import TaxRateRow, TaxTable

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)
START = date(2026, 12, 10)
END = date(2026, 12, 15)


def make_user(db: Session) -> User:
    user = User(
        email=f"owner-{uuid4().hex[:8]}@example.test",
        password_hash="x" * 20,
        role="owner",
    )
    db.add(user)
    db.flush()
    return user


def ruleset(*, version: int = 3, tcs_enabled: bool = False) -> PricingRuleSet:
    margins = {
        product: MarginRule(min_paise=5_000, min_bps=300, target_bps=800, max_bps=1_500)
        for product in PricingProduct
    }
    return PricingRuleSet(
        version=version,
        rounding=RoundingRule(mode=RoundingMode.NEAREST, step_paise=100),
        quote_validity_hours=168,
        revalidation=RevalidationTolerance(absolute_paise=50_000, percent_bps=200),
        forex_buffer_bps=150,
        gateway_fee_policy=GatewayFeePolicy.PASSTHROUGH,
        margin_combination=MarginCombination.MAX,
        margins=margins,
        segment_discount_budget_bps={segment: 200 for segment in CustomerSegment},
        competitor=CompetitorPolicy(
            max_age=timedelta(hours=24),
            min_comparability=80,
            undercut_paise=10_000,
        ),
        tax_table_version=1,
        gst_basis_by_product={product: GstBasis.COMMISSION for product in PricingProduct},
        tcs=TcsPolicy(
            enabled=tcs_enabled,
            rules_ref="synthetic-tcs-reference" if tcs_enabled else None,
        ),
    )


def tax_table() -> TaxTable:
    return TaxTable(
        version=1,
        approved_by_accountant=True,
        approved_on=date(2026, 11, 1),
        rows=tuple(
            TaxRateRow(
                product=product,
                basis=GstBasis.COMMISSION,
                rate_bps=500,
                reference=f"synthetic-{product.value}",
            )
            for product in PricingProduct
        ),
    )


def request(
    *,
    rules: PricingRuleSet | None = None,
    table: TaxTable | None = None,
    proposed: Money | None = None,
    competitor: Money | None = None,
) -> PriceRequest:
    return PriceRequest(
        rules=rules if rules is not None else ruleset(),
        tax_table=table if table is not None else tax_table(),
        items=(
            ItemCost(
                reference="hotel-1",
                product=PricingProduct.HOTEL,
                net=money(100_000),
            ),
        ),
        segment=CustomerSegment.NEW,
        competitor_reference=competitor,
        proposed_price=proposed,
        now=NOW,
    )


def save_rules(
    store: PricingStore, db: Session, rules: PricingRuleSet | None = None
) -> StoredRuleSet:
    return store.save_rule_set(
        rules if rules is not None else ruleset(),
        approved_by=make_user(db).id,
        approved_at=NOW,
        active_from=NOW - timedelta(hours=1),
    )


def save_tax(store: PricingStore, db: Session) -> StoredTaxTable:
    return store.save_tax_table(
        tax_table(),
        approved_by=make_user(db).id,
        active_from=NOW - timedelta(hours=1),
    )


def record_price(
    db: Session,
    *,
    rules: PricingRuleSet | None = None,
    proposed: Money | None = None,
    competitor: Money | None = None,
) -> tuple[PriceCalculationRecord, PriceResult]:
    store = PricingStore(db)
    stored_rules = save_rules(store, db, rules)
    stored_tax = save_tax(store, db)
    price_request = request(rules=rules, proposed=proposed, competitor=competitor)
    result = price_quote(price_request)
    row = store.record_calculation(
        price_request,
        result,
        rule_set_id=stored_rules.id,
        tax_table_id=stored_tax.id,
    )
    return row, result


def tasks(db: Session) -> list[HumanTask]:
    db.flush()
    return list(db.execute(select(HumanTask).order_by(HumanTask.created_at)).scalars())


def audit_rows(db: Session, action: str) -> list[AuditLog]:
    db.flush()
    return list(db.execute(select(AuditLog).where(AuditLog.action == action)).scalars())


# ---------------------------------------------------------------------------
# The two owner-only statuses
# ---------------------------------------------------------------------------


def test_a_below_floor_price_creates_one_owner_task_and_audits_it(db: Session) -> None:
    row, result = record_price(db, proposed=money(100_000))
    assert result.status is PriceStatus.BELOW_FLOOR

    routing = route_to_owner(db, result=result, calculation_id=row.id)

    assert routing is not None
    assert routing.created is True
    assert routing.action_type == "quote.below_floor"
    assert routing.status is PriceStatus.BELOW_FLOOR

    task = db.get(HumanTask, routing.task_id)
    assert task is not None
    assert task.task_type == "pricing.owner_decision"
    assert task.assignee_role == "owner"
    assert task.priority == "high"
    assert task.status == "open"
    assert task.subject_type == "price_calculation"
    assert task.subject_id == row.id
    assert task.due_at == result.expires_at
    assert "below-floor" in task.title.lower()

    entries = audit_rows(db, "pricing.owner_decision.requested")
    assert len(entries) == 1
    entry = entries[0]
    assert entry.subject_type == "price_calculation"
    assert entry.subject_id == row.id
    assert entry.after is not None
    assert entry.after["action_type"] == "quote.below_floor"
    assert entry.after["status"] == "below_floor"
    assert entry.after["floor_paise"] == result.floor.paise  # type: ignore[union-attr]
    assert entry.after["ceiling_paise"] == result.ceiling.paise  # type: ignore[union-attr]
    assert entry.after["customer_price_paise"] == 100_000
    assert "margins" not in entry.after


def test_an_uncompetitive_price_creates_an_owner_task_without_a_customer_price(
    db: Session,
) -> None:
    row, result = record_price(db, competitor=money(105_000))
    assert result.status is PriceStatus.UNCOMPETITIVE
    assert result.customer_price is None

    routing = route_to_owner(db, result=result, calculation_id=row.id)

    assert routing is not None and routing.created is True
    task = db.get(HumanTask, routing.task_id)
    assert task is not None
    assert "uncompetitive" in task.title.lower()

    entry = audit_rows(db, "pricing.owner_decision.requested")[0]
    assert entry.after is not None
    assert entry.after["status"] == "uncompetitive"
    assert entry.after["customer_price_paise"] is None


# ---------------------------------------------------------------------------
# Nothing to decide
# ---------------------------------------------------------------------------


def test_an_ok_price_is_not_routed(db: Session) -> None:
    row, result = record_price(db)
    assert result.status is PriceStatus.OK

    assert route_to_owner(db, result=result, calculation_id=row.id) is None
    assert tasks(db) == []
    assert audit_rows(db, "pricing.owner_decision.requested") == []


def test_a_needs_config_price_is_not_routed(db: Session) -> None:
    tcs_rules = ruleset(tcs_enabled=True)
    row, result = record_price(db, rules=tcs_rules)
    assert result.status is PriceStatus.NEEDS_CONFIG

    assert route_to_owner(db, result=result, calculation_id=row.id) is None
    assert tasks(db) == []
    assert audit_rows(db, "pricing.owner_decision.requested") == []


# ---------------------------------------------------------------------------
# Idempotency and fail-closed citation
# ---------------------------------------------------------------------------


def test_routing_the_same_calculation_twice_reuses_the_open_task(db: Session) -> None:
    row, result = record_price(db, proposed=money(100_000))

    first = route_to_owner(db, result=result, calculation_id=row.id)
    second = route_to_owner(db, result=result, calculation_id=row.id)

    assert first is not None and second is not None
    assert second.task_id == first.task_id
    assert second.created is False
    assert len(tasks(db)) == 1
    assert len(audit_rows(db, "pricing.owner_decision.requested")) == 1


def test_routing_refuses_a_calculation_that_was_never_stored(db: Session) -> None:
    _, result = record_price(db, proposed=money(100_000))

    with pytest.raises(PricingValidationError, match="was not found"):
        route_to_owner(db, result=result, calculation_id=uuid4())


def test_routing_refuses_a_result_that_disagrees_with_the_stored_row(db: Session) -> None:
    row, stored = record_price(db)
    assert stored.status is PriceStatus.OK
    proposed = price_quote(request(proposed=money(100_000)))
    assert proposed.status is PriceStatus.BELOW_FLOOR

    with pytest.raises(PricingValidationError, match="not below_floor"):
        route_to_owner(db, result=proposed, calculation_id=row.id)


def test_another_tenant_cannot_route_the_first_tenants_calculation(db: Session) -> None:
    row, result = record_price(db, proposed=money(100_000))

    with pytest.raises(PricingValidationError, match="was not found"):
        route_to_owner(
            db,
            result=result,
            calculation_id=row.id,
            tenant_id=uuid4(),
        )


# ---------------------------------------------------------------------------
# Stored competitor prices to the owner queue
# ---------------------------------------------------------------------------


def hotel_basis() -> ComparabilityBasis:
    return ComparabilityBasis(
        product_type="hotel",
        route="Goa",
        start_date=START,
        end_date=END,
        adults=2,
        fare_class="deluxe",
        checked_bags=1,
        refundable=True,
        currency="INR",
    )


def hotel_observation(price_paise: int, *, site: str = "SiteA") -> CompetitorPrice:
    return CompetitorPrice(
        site=site,
        product_type="hotel",
        route="Goa",
        start_date=START,
        end_date=END,
        adults=2,
        fare_class="deluxe",
        checked_bags=1,
        refundable=True,
        price_paise=price_paise,
        currency="INR",
        fetched_at=NOW - timedelta(minutes=5),
    )


def test_stored_competitor_prices_feed_the_ceiling_and_the_owner_route(db: Session) -> None:
    store = PricingStore(db)
    stored_rules = save_rules(store, db)
    stored_tax = save_tax(store, db)

    market = CompetitorSnapshotStore(db)
    basis = hotel_basis()
    market.record(
        basis,
        CompetitorFetch(
            site="SiteA",
            status=FetchStatus.OK,
            fetched_at=NOW - timedelta(minutes=5),
            observations=(hotel_observation(110_000),),
        ),
    )
    stored = market.latest(basis, now=NOW, max_age=timedelta(hours=24))
    assert stored is not None

    ceiling = trusted_ceiling(
        basis,
        stored,
        policy=stored_rules.rules.competitor,
        now=NOW,
    )
    assert ceiling.reference == money(110_000)

    price_request = request(
        rules=stored_rules.rules,
        table=stored_tax.table,
        competitor=ceiling.reference,
    )
    result = price_quote(price_request)
    assert result.status is PriceStatus.UNCOMPETITIVE
    assert result.ceiling == money(100_000)

    row = store.record_calculation(
        price_request,
        result,
        rule_set_id=stored_rules.id,
        tax_table_id=stored_tax.id,
    )
    routing = route_to_owner(db, result=result, calculation_id=row.id)

    assert routing is not None and routing.created is True
    assert routing.status is PriceStatus.UNCOMPETITIVE
    assert len(tasks(db)) == 1
