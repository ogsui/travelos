"""Persist and load pricing policy, FX observations and calculations (task 015c2).

A stored calculation must be reproducible: it cites the exact rule-set and
tax-table versions it used, and both payloads are verified against the sha256
recorded with them. Tax tables are only accepted with a recorded accountant
approval, FX stays an exact rational, and the active policy is the highest
version already in force at the requested moment.

Every fixture here is synthetic engineering data, not owner margins or
accountant tax advice.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from travelos.core.models import AuditLog, User
from travelos.core.money import money
from travelos.pricing.arithmetic import FxSnapshot
from travelos.pricing.calculator import ItemCost, PriceRequest, PriceStatus, price_quote
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
from travelos.pricing.records import (
    FxRateRecord,
    PricingRuleSetRecord,
    TaxRuleTableRecord,
)
from travelos.pricing.store import (
    PricingStore,
    StoredRuleSet,
    StoredTaxTable,
    canonical_hash,
)
from travelos.pricing.tax import TaxRateRow, TaxTable

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 7, 9, 0, tzinfo=UTC)


def make_user(db: Session) -> User:
    user = User(email=f"owner-{uuid4().hex[:8]}@example.test", password_hash="x" * 20, role="owner")
    db.add(user)
    db.flush()
    return user


def ruleset(
    *,
    version: int = 3,
    tax_table_version: int = 1,
    tcs_enabled: bool = False,
) -> PricingRuleSet:
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


def tax_table(
    *,
    version: int = 1,
    approved: bool = True,
    rate_bps: int = 500,
) -> TaxTable:
    return TaxTable(
        version=version,
        approved_by_accountant=approved,
        approved_on=date(2026, 11, 1),
        rows=tuple(
            TaxRateRow(
                product=product,
                basis=GstBasis.COMMISSION,
                rate_bps=rate_bps,
                reference=f"synthetic-{product.value}",
            )
            for product in PricingProduct
        ),
    )


def request(
    *,
    rules: PricingRuleSet | None = None,
    table: TaxTable | None = None,
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
        now=NOW,
    )


def fx(
    *,
    base: str = "USD",
    quote: str = "INR",
    numerator: int = 8_325,
    denominator: int = 100,
    fetched_at: datetime = NOW - timedelta(minutes=5),
    source: str = "synthetic test rate",
) -> FxSnapshot:
    return FxSnapshot(
        base_currency=base,
        quote_currency=quote,
        numerator=numerator,
        denominator=denominator,
        fetched_at=fetched_at,
        source=source,
    )


def save_rules(
    store: PricingStore,
    db: Session,
    *,
    rules: PricingRuleSet | None = None,
    active_from: datetime = NOW - timedelta(hours=1),
) -> StoredRuleSet:
    return store.save_rule_set(
        rules if rules is not None else ruleset(),
        approved_by=make_user(db).id,
        approved_at=NOW,
        active_from=active_from,
    )


def save_tax(
    store: PricingStore,
    db: Session,
    *,
    table: TaxTable | None = None,
    active_from: datetime = NOW - timedelta(hours=1),
) -> StoredTaxTable:
    return store.save_tax_table(
        table if table is not None else tax_table(),
        approved_by=make_user(db).id,
        active_from=active_from,
    )


# ---------------------------------------------------------------------------
# Rule sets
# ---------------------------------------------------------------------------


def test_a_saved_rule_set_round_trips_and_records_its_canonical_hash(db: Session) -> None:
    store = PricingStore(db)
    saved = save_rules(store, db)

    assert saved.version == 3
    assert saved.rules == ruleset()
    row = db.get(PricingRuleSetRecord, saved.id)
    assert row is not None
    assert row.source_hash == canonical_hash(ruleset().model_dump(mode="json"))

    active = store.active_rule_set(now=NOW)
    assert active is not None
    assert active.id == saved.id
    assert active.rules == ruleset()


def test_a_duplicate_rule_set_version_is_refused(db: Session) -> None:
    store = PricingStore(db)
    save_rules(store, db)

    with pytest.raises(PricingValidationError, match="version 3 already exists"):
        save_rules(store, db)

    count = db.execute(select(func.count()).select_from(PricingRuleSetRecord)).scalar_one()
    assert count == 1


def test_the_active_rule_set_is_the_highest_version_in_force(db: Session) -> None:
    store = PricingStore(db)
    first = save_rules(store, db, rules=ruleset(version=1))
    second = save_rules(
        store,
        db,
        rules=ruleset(version=2),
        active_from=NOW + timedelta(hours=1),
    )

    active_now = store.active_rule_set(now=NOW)
    assert active_now is not None and active_now.id == first.id

    active_later = store.active_rule_set(now=NOW + timedelta(hours=2))
    assert active_later is not None and active_later.id == second.id


def test_a_rule_set_activated_in_the_future_is_not_active(db: Session) -> None:
    store = PricingStore(db)
    save_rules(store, db, active_from=NOW + timedelta(hours=1))

    assert store.active_rule_set(now=NOW) is None


def test_a_naive_activation_time_is_refused(db: Session) -> None:
    store = PricingStore(db)

    with pytest.raises(PricingValidationError, match="timezone-aware"):
        save_rules(store, db, active_from=datetime(2026, 11, 7, 9, 0))


def test_a_tampered_rule_payload_fails_hash_verification(db: Session) -> None:
    store = PricingStore(db)
    payload = ruleset().model_dump(mode="json")
    db.execute(
        PricingRuleSetRecord.__table__.insert().values(
            version=payload["version"],
            rules={**payload, "quote_validity_hours": 1},
            source_hash=canonical_hash(payload),
            approved_by=make_user(db).id,
            approved_at=NOW,
            active_from=NOW - timedelta(hours=1),
        )
    )
    db.flush()

    with pytest.raises(PricingValidationError, match="hash does not match"):
        store.active_rule_set(now=NOW)


# ---------------------------------------------------------------------------
# Tax tables
# ---------------------------------------------------------------------------


def test_an_unapproved_tax_table_is_refused(db: Session) -> None:
    store = PricingStore(db)

    with pytest.raises(PricingValidationError, match="not approved by the accountant"):
        save_tax(store, db, table=tax_table(approved=False))

    count = db.execute(select(func.count()).select_from(TaxRuleTableRecord)).scalar_one()
    assert count == 0


def test_an_approved_tax_table_round_trips_with_its_approval_record(db: Session) -> None:
    store = PricingStore(db)
    saved = save_tax(store, db)

    row = db.get(TaxRuleTableRecord, saved.id)
    assert row is not None
    assert row.approved_by_accountant is True
    assert row.approved_on == date(2026, 11, 1)
    assert row.source_hash == canonical_hash(tax_table().model_dump(mode="json"))

    active = store.active_tax_table(now=NOW)
    assert active is not None
    assert active.table == tax_table()


def test_a_duplicate_tax_table_version_is_refused(db: Session) -> None:
    store = PricingStore(db)
    save_tax(store, db)

    with pytest.raises(PricingValidationError, match="version 1 already exists"):
        save_tax(store, db)


def test_a_tax_table_activated_in_the_future_is_not_active(db: Session) -> None:
    store = PricingStore(db)
    save_tax(store, db, active_from=NOW + timedelta(hours=1))

    assert store.active_tax_table(now=NOW) is None


def test_a_tampered_tax_table_fails_hash_verification(db: Session) -> None:
    store = PricingStore(db)
    payload = tax_table().model_dump(mode="json")
    payload["rows"][0]["rate_bps"] = 12_345
    db.execute(
        TaxRuleTableRecord.__table__.insert().values(
            version=1,
            rows=payload["rows"],
            source_hash=canonical_hash(tax_table().model_dump(mode="json")),
            approved_by_accountant=True,
            approved_on=date(2026, 11, 1),
            approved_by=make_user(db).id,
            active_from=NOW - timedelta(hours=1),
        )
    )
    db.flush()

    with pytest.raises(PricingValidationError, match="hash does not match"):
        store.active_tax_table(now=NOW)


# ---------------------------------------------------------------------------
# FX observations
# ---------------------------------------------------------------------------


def test_fx_is_stored_exactly_and_the_latest_observation_wins(db: Session) -> None:
    store = PricingStore(db)
    store.record_fx(fx(numerator=8_300, fetched_at=NOW - timedelta(hours=2)))
    newer = fx(numerator=8_325, fetched_at=NOW - timedelta(minutes=5))
    row = store.record_fx(newer)

    assert store.latest_fx("USD", "INR", now=NOW) == newer

    stored = db.execute(select(FxRateRecord).where(FxRateRecord.id == row.id)).scalar_one()
    assert (stored.numerator, stored.denominator) == (8_325, 100)


def test_fx_selection_is_per_currency_pair_and_skips_the_future(db: Session) -> None:
    store = PricingStore(db)
    usd = fx(base="USD", numerator=8_325)
    eur = fx(base="EUR", numerator=9_100)
    store.record_fx(usd)
    store.record_fx(eur)
    store.record_fx(fx(base="GBP", fetched_at=NOW + timedelta(minutes=5)))

    assert store.latest_fx("USD", "INR", now=NOW) == usd
    assert store.latest_fx("EUR", "INR", now=NOW) == eur
    assert store.latest_fx("GBP", "INR", now=NOW) is None


def test_fx_max_age_filters_a_stale_observation(db: Session) -> None:
    store = PricingStore(db)
    stale = fx(fetched_at=NOW - timedelta(hours=48))
    store.record_fx(stale)

    assert store.latest_fx("USD", "INR", now=NOW, max_age=timedelta(hours=24)) is None
    assert store.latest_fx("USD", "INR", now=NOW) == stale


# ---------------------------------------------------------------------------
# Price calculations
# ---------------------------------------------------------------------------


def test_a_calculation_records_the_request_result_and_cited_versions(db: Session) -> None:
    store = PricingStore(db)
    rules = save_rules(store, db)
    table = save_tax(store, db)
    price_request = request()
    result = price_quote(price_request)

    row = store.record_calculation(
        price_request,
        result,
        rule_set_id=rules.id,
        tax_table_id=table.id,
    )

    assert row.status == PriceStatus.OK.value
    assert row.rule_version == 3
    assert row.tax_table_version == 1
    assert row.floor_paise == result.floor.paise  # type: ignore[union-attr]
    assert row.customer_price_paise == result.customer_price.paise  # type: ignore[union-attr]
    assert row.quote_revision_id is None
    assert row.inputs["segment"] == "new"
    assert row.inputs["items"][0]["reference"] == "hotel-1"
    assert row.outputs["lines"][0]["kind"] == "supplier"

    loaded = store.get_calculation(row.id)
    assert loaded is not None and loaded.id == row.id


def test_a_calculation_can_cite_a_quote_revision(db: Session) -> None:
    store = PricingStore(db)
    rules = save_rules(store, db)
    table = save_tax(store, db)
    quote_revision_id = uuid4()
    price_request = request()

    row = store.record_calculation(
        price_request,
        price_quote(price_request),
        rule_set_id=rules.id,
        tax_table_id=table.id,
        quote_revision_id=quote_revision_id,
    )

    assert row.quote_revision_id == quote_revision_id


def test_a_calculation_with_mismatched_versions_is_refused(db: Session) -> None:
    store = PricingStore(db)
    rules = save_rules(store, db)
    table = save_tax(store, db)
    price_request = request(rules=ruleset(version=4))

    with pytest.raises(PricingValidationError, match="rule set version"):
        store.record_calculation(
            price_request,
            price_quote(price_request),
            rule_set_id=rules.id,
            tax_table_id=table.id,
        )


def test_a_calculation_whose_rule_set_names_another_tax_version_is_refused(
    db: Session,
) -> None:
    store = PricingStore(db)
    rules = save_rules(store, db, rules=ruleset(tax_table_version=2))
    table = save_tax(store, db)
    price_request = request(rules=ruleset(tax_table_version=2), table=tax_table())

    with pytest.raises(PricingValidationError, match="tax table version"):
        store.record_calculation(
            price_request,
            price_quote(price_request),
            rule_set_id=rules.id,
            tax_table_id=table.id,
        )


def test_a_needs_config_calculation_is_stored_without_prices(db: Session) -> None:
    store = PricingStore(db)
    rules = save_rules(store, db, rules=ruleset(tcs_enabled=True))
    table = save_tax(store, db)
    price_request = request(rules=ruleset(tcs_enabled=True))

    result = price_quote(price_request)
    assert result.status is PriceStatus.NEEDS_CONFIG

    row = store.record_calculation(
        price_request,
        result,
        rule_set_id=rules.id,
        tax_table_id=table.id,
    )

    assert row.status == PriceStatus.NEEDS_CONFIG.value
    assert row.floor_paise is None
    assert row.customer_price_paise is None
    assert row.outputs["gaps"]


# ---------------------------------------------------------------------------
# Audit and tenant isolation
# ---------------------------------------------------------------------------


def test_every_write_is_audited_without_copying_the_policy_payload(db: Session) -> None:
    store = PricingStore(db)
    rules = save_rules(store, db)
    table = save_tax(store, db)
    rate = store.record_fx(fx())
    price_request = request()
    store.record_calculation(
        price_request,
        price_quote(price_request),
        rule_set_id=rules.id,
        tax_table_id=table.id,
    )

    db.flush()
    entries = list(db.execute(select(AuditLog)).scalars())
    by_action = {entry.action: entry for entry in entries}
    assert set(by_action) == {
        "pricing.ruleset.saved",
        "pricing.tax_table.saved",
        "pricing.fx.recorded",
        "pricing.calculation.recorded",
    }
    assert "margins" not in (by_action["pricing.ruleset.saved"].after or {})
    assert by_action["pricing.fx.recorded"].after == {
        "base_currency": "USD",
        "quote_currency": "INR",
        "numerator": 8_325,
        "denominator": 100,
        "fetched_at": rate.fetched_at.isoformat(),
    }


def test_another_tenant_cannot_read_the_first_tenants_records(db: Session) -> None:
    store = PricingStore(db)
    save_rules(store, db)
    save_tax(store, db)
    store.record_fx(fx())
    price_request = request()
    calculation = store.record_calculation(
        price_request,
        price_quote(price_request),
        rule_set_id=store.active_rule_set(now=NOW).id,  # type: ignore[union-attr]
        tax_table_id=store.active_tax_table(now=NOW).id,  # type: ignore[union-attr]
    )

    other = PricingStore(db, tenant_id=uuid4())

    assert other.active_rule_set(now=NOW) is None
    assert other.active_tax_table(now=NOW) is None
    assert other.latest_fx("USD", "INR", now=NOW) is None
    assert other.get_calculation(calculation.id) is None


def test_a_calculation_cannot_cite_another_tenants_policy(db: Session) -> None:
    store = PricingStore(db)
    rules = save_rules(store, db)
    table = save_tax(store, db)
    price_request = request()
    other = PricingStore(db, tenant_id=uuid4())

    with pytest.raises(PricingValidationError, match="was not found"):
        other.record_calculation(
            price_request,
            price_quote(price_request),
            rule_set_id=rules.id,
            tax_table_id=table.id,
        )
