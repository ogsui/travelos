"""Persist quote revisions that match a stored price calculation exactly (016a).

A quote is one option under an inquiry. Each revision is immutable and copies
the calculator's breakdown lines, so the customer total can always be checked
against the calculation that produced it. Adding a revision stores an exact
integer-paise diff; the quote expiry is the calculation's expiry, never an
invented validity.

Every margin, tax rate and competitor value here is a synthetic engineering
fixture, not owner or accountant policy.
"""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.errors import AppendOnlyError
from travelos.core.models import AuditLog, User
from travelos.core.money import Money, money
from travelos.inquiry.requirement import Requirement
from travelos.packages.builder import build_package
from travelos.packages.models import Itinerary
from travelos.packages.store import ItineraryStore
from travelos.pricing.calculator import (
    ItemCost,
    PriceRequest,
    PriceResult,
    price_quote,
)
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
from travelos.pricing.store import PricingStore
from travelos.pricing.tax import TaxRateRow, TaxTable
from travelos.quotes.errors import (
    QuoteNotFoundError,
    QuotePricingError,
    QuoteStateError,
    QuoteValidationError,
)
from travelos.quotes.models import AcceptanceStatus, QuoteState, acceptance_status
from travelos.quotes.records import QuoteLineRecord, QuoteRecord, QuoteRevisionRecord
from travelos.quotes.store import QuoteStore
from travelos.suppliers.models import CancellationRules, FareRules, Offer

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
START = date(2027, 2, 10)
END = date(2027, 2, 13)
_VERSIONS = itertools.count(1)


def make_user(db: Session) -> User:
    user = User(
        email=f"owner-{uuid4().hex[:8]}@example.test",
        password_hash="x" * 20,
        role="owner",
    )
    db.add(user)
    db.flush()
    return user


def ruleset(*, version: int, tax_table_version: int, tcs_enabled: bool = False) -> PricingRuleSet:
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
        tax_table_version=tax_table_version,
        gst_basis_by_product={product: GstBasis.COMMISSION for product in PricingProduct},
        tcs=TcsPolicy(
            enabled=tcs_enabled,
            rules_ref="synthetic-tcs-reference" if tcs_enabled else None,
        ),
    )


def tax_table(*, version: int) -> TaxTable:
    return TaxTable(
        version=version,
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


def record_price(
    db: Session,
    *,
    net_paise: int = 100_000,
    now: datetime = NOW,
    proposed: Money | None = None,
    competitor: Money | None = None,
    tcs_enabled: bool = False,
    quote_revision_id: UUID | None = None,
) -> tuple[PriceCalculationRecord, PriceResult]:
    version = next(_VERSIONS)
    pricing = PricingStore(db)
    stored_rules = pricing.save_rule_set(
        ruleset(version=version, tax_table_version=version, tcs_enabled=tcs_enabled),
        approved_by=make_user(db).id,
        approved_at=now,
        active_from=now - timedelta(hours=1),
    )
    stored_tax = pricing.save_tax_table(
        tax_table(version=version),
        approved_by=make_user(db).id,
        active_from=now - timedelta(hours=1),
    )
    request = PriceRequest(
        rules=stored_rules.rules,
        tax_table=stored_tax.table,
        items=(
            ItemCost(
                reference="hotel-1",
                product=PricingProduct.HOTEL,
                net=money(net_paise),
            ),
        ),
        segment=CustomerSegment.NEW,
        competitor_reference=competitor,
        proposed_price=proposed,
        now=now,
    )
    result = price_quote(request)
    row = pricing.record_calculation(
        request,
        result,
        rule_set_id=stored_rules.id,
        tax_table_id=stored_tax.id,
        quote_revision_id=quote_revision_id,
    )
    return row, result


# ---------------------------------------------------------------------------
# A real itinerary for the quote to reference
# ---------------------------------------------------------------------------


def sample_requirement() -> Requirement:
    return Requirement(
        trip_type="package",
        origin_city="Delhi",
        destinations=["Goa"],
        start_date=START,
        end_date=END,
        adults=2,
        rooms=1,
    )


def sample_offer(
    *,
    supplier: str = "MockAir",
    offer_id: str = "air-out",
    product_type: str = "flight",
    name: str = "MockAir Delhi to Goa",
    city: str = "Goa",
    route: str | None = "Delhi-Goa",
    net_paise: int = 1_000_000,
) -> Offer:
    return Offer(
        supplier=supplier,
        offer_id=offer_id,
        product_type=product_type,
        name=name,
        city=city,
        route=route,
        net_paise=net_paise,
        taxes_paise=0,
        available_count=9,
        fare_rules=FareRules(
            cancellation=CancellationRules(
                refundable=True,
                cancellation_days=7,
                change_fee_paise=0,
            )
        ),
        fetched_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )


def sample_itinerary() -> Itinerary:
    offers = [
        sample_offer(),
        sample_offer(offer_id="air-back", name="MockAir Goa to Delhi", route="Goa-Delhi"),
        sample_offer(
            supplier="Mock Hotels",
            offer_id="hotel-goa",
            product_type="hotel",
            name="Mock Hotels Goa",
            route=None,
            net_paise=500_000,
        ),
        sample_offer(
            supplier="Mock Transfers",
            offer_id="transfer-goa",
            product_type="transfer",
            name="Mock Transfers Goa",
            route=None,
            net_paise=100_000,
        ),
    ]
    build = build_package(sample_requirement(), offers, now=NOW)
    assert build.itinerary is not None
    return build.itinerary


def make_itinerary(db: Session) -> UUID:
    return ItineraryStore(db).record(sample_itinerary()).id


# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def test_a_quote_revision_copies_the_calculations_total_and_lines_exactly(
    db: Session,
) -> None:
    itinerary_id = make_itinerary(db)
    calculation, result = record_price(db)
    assert result.customer_price is not None

    quote = QuoteStore(db).create(
        calculation_id=calculation.id,
        itinerary_id=itinerary_id,
    )

    assert quote.state == QuoteState.DRAFT.value
    assert quote.current_revision == 1
    assert quote.expires_at == result.expires_at
    assert quote.accept_token_hash is None

    view = QuoteStore(db).load(quote.id)
    assert len(view.revisions) == 1
    current = view.current
    assert current.number == 1
    assert current.total == result.customer_price
    assert current.total.paise == calculation.customer_price_paise
    assert sum(line.amount.paise for line in current.lines) == current.total.paise
    assert len(current.lines) == len(result.lines)
    assert [line.kind.value for line in current.lines] == [line.kind for line in result.lines]

    revision_id = db.execute(
        select(QuoteRevisionRecord.id).where(QuoteRevisionRecord.quote_id == quote.id)
    ).scalar_one()
    rows = list(
        db.execute(
            select(QuoteLineRecord).where(QuoteLineRecord.revision_id == revision_id)
        ).scalars()
    )
    assert len(rows) == len(result.lines)
    assert sum(row.amount_paise for row in rows) == result.customer_price.paise


def test_creating_a_quote_records_an_audit_entry_without_customer_data(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    calculation, result = record_price(db)
    assert result.customer_price is not None

    quote = QuoteStore(db).create(
        calculation_id=calculation.id,
        itinerary_id=itinerary_id,
        inquiry_id=None,
    )

    db.flush()
    entries = list(
        db.execute(
            select(AuditLog).where(
                AuditLog.action == "quote.created",
                AuditLog.subject_id == quote.id,
            )
        ).scalars()
    )
    assert len(entries) == 1
    after = entries[0].after
    assert after is not None
    assert after["revision_number"] == 1
    assert after["price_calculation_id"] == str(calculation.id)
    assert after["total_paise"] == result.customer_price.paise
    assert "customer" not in after


def test_a_prelinked_calculation_reuses_the_revision_id_it_was_recorded_with(
    db: Session,
) -> None:
    from travelos.core.ids import uuid7

    itinerary_id = make_itinerary(db)
    prelinked = uuid7()
    calculation, _ = record_price(db, quote_revision_id=prelinked)

    quote = QuoteStore(db).create(
        calculation_id=calculation.id,
        itinerary_id=itinerary_id,
    )

    revision = db.get(QuoteRevisionRecord, prelinked)
    assert revision is not None
    assert revision.quote_id == quote.id
    assert revision.price_calculation_id == calculation.id
    assert calculation.quote_revision_id == prelinked


def test_an_ok_quote_is_acceptable_only_after_it_is_sent(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    calculation, result = record_price(db)
    quote = QuoteStore(db).create(
        calculation_id=calculation.id,
        itinerary_id=itinerary_id,
    )

    assert quote.expires_at == result.expires_at
    assert quote.expires_at is not None
    assert (
        acceptance_status(
            state=QuoteState(quote.state),
            expires_at=quote.expires_at,
            now=NOW,
        )
        is AcceptanceStatus.NOT_SENT
    )

    quote.state = QuoteState.SENT.value
    db.flush()
    assert (
        acceptance_status(
            state=QuoteState(quote.state),
            expires_at=quote.expires_at,
            now=NOW,
        )
        is AcceptanceStatus.ELIGIBLE
    )

    quote.expires_at = NOW
    db.flush()
    assert (
        acceptance_status(
            state=QuoteState(quote.state),
            expires_at=quote.expires_at,
            now=NOW,
        )
        is AcceptanceStatus.EXPIRED
    )


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"proposed": money(100_000)}, "below_floor"),
        ({"competitor": money(105_000)}, "uncompetitive"),
        ({"tcs_enabled": True}, "needs_config"),
    ],
)
def test_a_non_ok_calculation_cannot_become_a_quote(
    db: Session,
    kwargs: dict[str, object],
    match: str,
) -> None:
    itinerary_id = make_itinerary(db)
    calculation, _ = record_price(db, **kwargs)

    with pytest.raises(QuotePricingError, match=match):
        QuoteStore(db).create(
            calculation_id=calculation.id,
            itinerary_id=itinerary_id,
        )

    db.flush()
    assert db.execute(select(QuoteRecord)).scalars().first() is None


def test_an_unknown_calculation_is_refused(db: Session) -> None:
    itinerary_id = make_itinerary(db)

    with pytest.raises(QuotePricingError, match="was not found"):
        QuoteStore(db).create(calculation_id=uuid4(), itinerary_id=itinerary_id)


def test_an_unknown_itinerary_is_refused(db: Session) -> None:
    calculation, _ = record_price(db)

    with pytest.raises(QuoteValidationError, match="is not recorded"):
        QuoteStore(db).create(calculation_id=calculation.id, itinerary_id=uuid4())


def test_a_calculation_without_an_expiry_is_refused(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    calculation, _ = record_price(db)

    broken = PriceCalculationRecord(
        tenant_id=DEFAULT_TENANT_ID,
        quote_revision_id=None,
        rule_set_id=calculation.rule_set_id,
        tax_table_id=calculation.tax_table_id,
        status="ok",
        currency="INR",
        rule_version=calculation.rule_version,
        tax_table_version=calculation.tax_table_version,
        floor_paise=calculation.floor_paise,
        target_paise=calculation.target_paise,
        ceiling_paise=calculation.ceiling_paise,
        recommended_paise=calculation.recommended_paise,
        customer_price_paise=calculation.customer_price_paise,
        inputs={},
        outputs={"status": "ok", "lines": []},
    )
    db.add(broken)
    db.flush()

    with pytest.raises(QuotePricingError, match="no expiry"):
        QuoteStore(db).create(calculation_id=broken.id, itinerary_id=itinerary_id)


def test_a_calculation_cannot_back_two_revisions_or_two_quotes(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    calculation, _ = record_price(db)
    store = QuoteStore(db)
    quote = store.create(calculation_id=calculation.id, itinerary_id=itinerary_id)

    with pytest.raises(QuoteValidationError, match="already backs"):
        store.create(calculation_id=calculation.id, itinerary_id=itinerary_id)

    with pytest.raises(QuoteValidationError, match="already backs"):
        store.add_revision(
            quote.id,
            calculation_id=calculation.id,
            itinerary_id=itinerary_id,
        )


# ---------------------------------------------------------------------------
# Second revision and diff
# ---------------------------------------------------------------------------


def test_a_second_draft_revision_records_the_diff_and_moves_the_expiry(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    first_calculation, first_result = record_price(db)
    store = QuoteStore(db)
    quote = store.create(calculation_id=first_calculation.id, itinerary_id=itinerary_id)

    second_calculation, second_result = record_price(
        db,
        net_paise=110_000,
        now=NOW + timedelta(hours=1),
    )
    store.add_revision(
        quote.id,
        calculation_id=second_calculation.id,
        itinerary_id=itinerary_id,
    )

    assert quote.current_revision == 2
    assert quote.expires_at == second_result.expires_at
    assert first_result.expires_at is not None
    assert second_result.expires_at is not None
    assert quote.expires_at > first_result.expires_at

    view = store.load(quote.id)
    assert [revision.number for revision in view.revisions] == [1, 2]
    assert first_result.customer_price is not None
    assert second_result.customer_price is not None
    assert view.revisions[0].total == first_result.customer_price
    assert view.revisions[1].total == second_result.customer_price

    revision_row = db.execute(
        select(QuoteRevisionRecord).where(
            QuoteRevisionRecord.quote_id == quote.id,
            QuoteRevisionRecord.number == 2,
        )
    ).scalar_one()
    assert revision_row.diff_from_previous is not None
    assert revision_row.diff_from_previous["previous_number"] == 1
    assert revision_row.diff_from_previous["current_number"] == 2
    assert (
        revision_row.diff_from_previous["total_delta"]["paise"]
        == second_result.customer_price.paise - first_result.customer_price.paise
    )

    db.flush()
    entries = list(
        db.execute(
            select(AuditLog).where(
                AuditLog.action == "quote.revision.created",
                AuditLog.subject_id == quote.id,
            )
        ).scalars()
    )
    assert len(entries) == 1
    after = entries[0].after
    assert after is not None
    assert after["revision_number"] == 2


def test_a_revision_is_refused_once_the_quote_is_no_longer_draft(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    calculation, _ = record_price(db)
    store = QuoteStore(db)
    quote = store.create(calculation_id=calculation.id, itinerary_id=itinerary_id)

    quote.state = QuoteState.SENT.value
    db.flush()
    second_calculation, _ = record_price(db, net_paise=110_000)

    with pytest.raises(QuoteStateError, match="DRAFT"):
        store.add_revision(
            quote.id,
            calculation_id=second_calculation.id,
            itinerary_id=itinerary_id,
        )


def test_an_unknown_quote_cannot_gain_a_revision(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    calculation, _ = record_price(db)

    with pytest.raises(QuoteNotFoundError):
        QuoteStore(db).add_revision(
            uuid4(),
            calculation_id=calculation.id,
            itinerary_id=itinerary_id,
        )


# ---------------------------------------------------------------------------
# Tenant isolation and immutability
# ---------------------------------------------------------------------------


def test_a_quote_is_invisible_to_another_tenant(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    calculation, _ = record_price(db)
    quote = QuoteStore(db).create(
        calculation_id=calculation.id,
        itinerary_id=itinerary_id,
    )

    other = QuoteStore(db, tenant_id=uuid4())

    with pytest.raises(QuoteNotFoundError):
        other.load(quote.id)

    with pytest.raises(QuotePricingError, match="was not found"):
        other.create(calculation_id=calculation.id, itinerary_id=itinerary_id)


def test_a_revision_and_its_lines_are_append_only(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    calculation, _ = record_price(db)
    quote = QuoteStore(db).create(
        calculation_id=calculation.id,
        itinerary_id=itinerary_id,
    )
    revision = db.execute(
        select(QuoteRevisionRecord).where(QuoteRevisionRecord.quote_id == quote.id)
    ).scalar_one()

    revision.number = 99
    with pytest.raises(AppendOnlyError):
        db.flush()
    db.rollback()


def test_a_quote_line_cannot_be_deleted(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    calculation, _ = record_price(db)
    quote = QuoteStore(db).create(
        calculation_id=calculation.id,
        itinerary_id=itinerary_id,
    )
    revision = db.execute(
        select(QuoteRevisionRecord).where(QuoteRevisionRecord.quote_id == quote.id)
    ).scalar_one()
    line = (
        db.execute(select(QuoteLineRecord).where(QuoteLineRecord.revision_id == revision.id))
        .scalars()
        .first()
    )
    assert line is not None

    db.delete(line)
    with pytest.raises(AppendOnlyError):
        db.flush()
    db.rollback()


# ---------------------------------------------------------------------------
# Database constraints
# ---------------------------------------------------------------------------


def test_the_database_refuses_an_unknown_quote_state(db: Session) -> None:
    db.add(
        QuoteRecord(
            tenant_id=DEFAULT_TENANT_ID,
            inquiry_id=None,
            current_revision=0,
            state="NOPE",
            expires_at=NOW,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_the_database_refuses_a_malformed_accept_token_hash(db: Session) -> None:
    db.add(
        QuoteRecord(
            tenant_id=DEFAULT_TENANT_ID,
            inquiry_id=None,
            current_revision=0,
            state=QuoteState.DRAFT.value,
            expires_at=NOW,
            accept_token_hash="not-a-sha256",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
