"""Schema for pricing rules, tax tables, FX rates and price calculations (task 015c1).

These four tables make a price reproducible: a stored calculation cites the exact
rule-set and tax-table versions it used, and every one of them is append-only.
The tests pin the invariants that keep that promise: a version cannot be reused,
a malformed hash or foreign amount cannot be stored, a status and its prices must
agree, and UPDATE/DELETE/TRUNCATE fail at both the ORM and the database.

All payloads here are obviously synthetic; no owner margin, tax rate or exchange
rate is a real business value.
"""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from travelos.core.errors import AppendOnlyError
from travelos.core.models import User
from travelos.pricing.records import (
    FxRateRecord,
    PriceCalculationRecord,
    PricingRuleSetRecord,
    TaxRuleTableRecord,
)

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)
SOURCE_HASH = "a" * 64
_VERSIONS = itertools.count(1)


def make_user(db: Session) -> User:
    user = User(email=f"owner-{uuid4().hex[:8]}@example.test", password_hash="x" * 20, role="owner")
    db.add(user)
    db.flush()
    return user


def rule_set(db: Session, **overrides: Any) -> PricingRuleSetRecord:
    values: dict[str, Any] = {
        "version": next(_VERSIONS),
        "rules": {
            "version": 1,
            "currency": "INR",
            "rounding": {"mode": "nearest", "step_paise": 100},
        },
        "source_hash": SOURCE_HASH,
        "approved_by": make_user(db).id,
        "approved_at": NOW,
        "active_from": NOW,
    }
    values.update(overrides)
    row = PricingRuleSetRecord(**values)
    db.add(row)
    db.flush()
    return row


def tax_table(db: Session, **overrides: Any) -> TaxRuleTableRecord:
    values: dict[str, Any] = {
        "version": next(_VERSIONS),
        "rows": [
            {
                "product": "hotel",
                "basis": "commission",
                "rate_bps": 500,
                "reference": "synthetic-row",
            }
        ],
        "source_hash": SOURCE_HASH,
        "approved_by_accountant": True,
        "approved_on": date(2026, 11, 1),
        "approved_by": make_user(db).id,
        "active_from": NOW,
    }
    values.update(overrides)
    row = TaxRuleTableRecord(**values)
    db.add(row)
    db.flush()
    return row


def fx_rate(db: Session, **overrides: Any) -> FxRateRecord:
    values: dict[str, Any] = {
        "base_currency": "USD",
        "quote_currency": "INR",
        "numerator": 8_325,
        "denominator": 100,
        "source": "synthetic test rate",
        "fetched_at": NOW,
    }
    values.update(overrides)
    row = FxRateRecord(**values)
    db.add(row)
    db.flush()
    return row


def calculation(db: Session, **overrides: Any) -> PriceCalculationRecord:
    values: dict[str, Any] = {
        "quote_revision_id": None,
        "rule_set_id": rule_set(db).id,
        "tax_table_id": tax_table(db).id,
        "status": "ok",
        "currency": "INR",
        "rule_version": 1,
        "tax_table_version": 1,
        "floor_paise": 105_250,
        "target_paise": 108_400,
        "ceiling_paise": 115_750,
        "recommended_paise": 108_400,
        "customer_price_paise": 108_400,
        "inputs": {"segment": "new", "now": NOW.isoformat()},
        "outputs": {
            "status": "ok",
            "floor": {"paise": 105_250, "currency": "INR"},
            "lines": [{"kind": "supplier", "amount": {"paise": 100_000, "currency": "INR"}}],
        },
    }
    values.update(overrides)
    row = PriceCalculationRecord(**values)
    db.add(row)
    db.flush()
    return row


# ---------------------------------------------------------------------------
# Shape
# ---------------------------------------------------------------------------


def test_the_pricing_tables_have_their_documented_columns(db: Session) -> None:
    expected = {
        PricingRuleSetRecord: {"version", "rules", "source_hash", "approved_by", "active_from"},
        TaxRuleTableRecord: {
            "version",
            "rows",
            "source_hash",
            "approved_by_accountant",
            "approved_on",
            "approved_by",
            "active_from",
        },
        FxRateRecord: {
            "base_currency",
            "quote_currency",
            "numerator",
            "denominator",
            "source",
            "fetched_at",
        },
        PriceCalculationRecord: {
            "quote_revision_id",
            "rule_set_id",
            "tax_table_id",
            "status",
            "rule_version",
            "tax_table_version",
            "floor_paise",
            "customer_price_paise",
            "inputs",
            "outputs",
        },
    }
    for model, columns in expected.items():
        assert columns <= set(model.__table__.columns.keys()), model.__tablename__


def test_the_jsonb_payloads_round_trip(db: Session) -> None:
    rules = rule_set(db)
    table = tax_table(db)
    row = calculation(db, rule_set_id=rules.id, tax_table_id=table.id)
    rate = fx_rate(db)

    loaded_rules = db.get(PricingRuleSetRecord, rules.id)
    loaded_table = db.get(TaxRuleTableRecord, table.id)
    loaded_calc = db.get(PriceCalculationRecord, row.id)
    loaded_rate = db.get(FxRateRecord, rate.id)

    assert loaded_rules is not None and loaded_rules.rules == rules.rules
    assert loaded_table is not None and loaded_table.rows == table.rows
    assert loaded_calc is not None and loaded_calc.inputs == row.inputs
    assert loaded_calc.outputs["lines"][0]["amount"]["paise"] == 100_000
    assert loaded_rate is not None and (loaded_rate.numerator, loaded_rate.denominator) == (
        8_325,
        100,
    )


# ---------------------------------------------------------------------------
# Versioning and value constraints
# ---------------------------------------------------------------------------


def test_a_rule_set_version_cannot_be_reused(db: Session) -> None:
    rule_set(db, version=4)

    with pytest.raises(IntegrityError, match="uq_pricing_rule_sets_version"):
        rule_set(db, version=4)
    db.rollback()


def test_a_tax_table_version_cannot_be_reused(db: Session) -> None:
    tax_table(db, version=2)

    with pytest.raises(IntegrityError, match="uq_tax_rule_tables_version"):
        tax_table(db, version=2)
    db.rollback()


def test_a_source_hash_must_be_a_sha256_hex_digest(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_pricing_rule_sets_source_hash_hex"):
        rule_set(db, source_hash="not-a-hash")
    db.rollback()


def test_a_zero_version_is_rejected(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_pricing_rule_sets_version_positive"):
        rule_set(db, version=0)
    db.rollback()


def test_an_fx_rate_must_be_an_upside_rational(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_fx_rates_numerator_positive"):
        fx_rate(db, numerator=0)
    db.rollback()

    with pytest.raises(IntegrityError, match="ck_fx_rates_denominator_positive"):
        fx_rate(db, denominator=0)
    db.rollback()


def test_an_fx_pair_uses_three_letter_currency_codes(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_fx_rates_base_currency_code"):
        fx_rate(db, base_currency="usd")
    db.rollback()


def test_a_calculation_needs_an_existing_rule_set_and_tax_table(db: Session) -> None:
    with pytest.raises(IntegrityError, match="fk_price_calculations_rule_set_id"):
        calculation(db, rule_set_id=uuid4())
    db.rollback()


# ---------------------------------------------------------------------------
# Status and price consistency
# ---------------------------------------------------------------------------


def test_an_unknown_price_status_is_rejected(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_price_calculations_status"):
        calculation(
            db,
            status="probably_fine",
            recommended_paise=None,
            customer_price_paise=None,
        )
    db.rollback()


def test_a_needs_config_calculation_cannot_carry_prices(db: Session) -> None:
    with pytest.raises(IntegrityError, match="levels"):
        calculation(
            db,
            status="needs_config",
            recommended_paise=None,
            customer_price_paise=None,
        )
    db.rollback()


def test_a_needs_config_calculation_stores_no_prices(db: Session) -> None:
    row = calculation(
        db,
        status="needs_config",
        floor_paise=None,
        target_paise=None,
        ceiling_paise=None,
        recommended_paise=None,
        customer_price_paise=None,
    )

    assert row.floor_paise is None
    assert row.customer_price_paise is None


def test_a_priced_calculation_requires_its_levels(db: Session) -> None:
    with pytest.raises(IntegrityError, match="levels"):
        calculation(db, floor_paise=None)
    db.rollback()


def test_an_uncompetitive_calculation_has_no_customer_price(db: Session) -> None:
    with pytest.raises(IntegrityError, match="customer_price_matches_status"):
        calculation(db, status="uncompetitive")
    db.rollback()

    row = calculation(
        db,
        status="uncompetitive",
        recommended_paise=None,
        customer_price_paise=None,
    )
    assert row.customer_price_paise is None


def test_a_negative_price_is_rejected(db: Session) -> None:
    with pytest.raises(IntegrityError, match="ck_price_calculations_floor_non_negative"):
        calculation(db, floor_paise=-1)
    db.rollback()


# ---------------------------------------------------------------------------
# Append-only
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "factory",
    [rule_set, tax_table, fx_rate, calculation],
    ids=["rule_set", "tax_table", "fx_rate", "calculation"],
)
def test_an_orm_update_is_refused(db: Session, factory: Any) -> None:
    row = factory(db)
    row.updated_at = NOW  # any UPDATE attempt, even a harmless-looking one

    with pytest.raises(AppendOnlyError, match="append-only"):
        db.flush()
    db.rollback()


@pytest.mark.parametrize(
    "factory",
    [rule_set, tax_table, fx_rate, calculation],
    ids=["rule_set", "tax_table", "fx_rate", "calculation"],
)
def test_an_orm_delete_is_refused(db: Session, factory: Any) -> None:
    row = factory(db)

    db.delete(row)
    with pytest.raises(AppendOnlyError, match="append-only"):
        db.flush()
    db.rollback()


@pytest.mark.parametrize(
    ("table_name", "factory"),
    [
        ("pricing_rule_sets", rule_set),
        ("tax_rule_tables", tax_table),
        ("fx_rates", fx_rate),
        ("price_calculations", calculation),
    ],
    ids=["rule_set", "tax_table", "fx_rate", "calculation"],
)
def test_the_database_blocks_raw_mutation_and_truncate(
    db: Session, table_name: str, factory: Any
) -> None:
    factory(db)

    for statement in (
        f"UPDATE {table_name} SET created_at = now()",
        f"DELETE FROM {table_name}",
        # CASCADE clears the FK objection so the trigger itself is what refuses.
        f"TRUNCATE {table_name} CASCADE",
    ):
        # A savepoint keeps the fixture row alive for the next statement.
        with pytest.raises(DBAPIError, match="append-only"), db.begin_nested():
            db.execute(text(statement))


def test_a_correction_is_a_new_row_not_an_edit(db: Session) -> None:
    rules = rule_set(db, version=7)

    corrected = rule_set(db, version=8, rules={**rules.rules, "rounding_step": 500})

    assert corrected.id != rules.id
    stored = db.execute(
        select(PricingRuleSetRecord.version).order_by(PricingRuleSetRecord.version)
    ).scalars()
    assert list(stored) == [7, 8]


def test_the_approved_by_foreign_key_points_at_a_user(db: Session) -> None:
    with pytest.raises(IntegrityError, match="fk_pricing_rule_sets_approved_by_users"):
        rule_set(db, approved_by=UUID(int=0))
    db.rollback()
