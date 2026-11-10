"""Persisted pricing rules, tax tables, FX rates and price calculations (task 015c1).

Four append-only tables make a customer price reproducible:

* ``pricing_rule_sets`` stores one complete, owner-approved ``PricingRuleSet``
  version (JSONB) with a sha256 of its canonical JSON, so a stored calculation can
  cite the exact rules it used even after the YAML file changes.
* ``tax_rule_tables`` stores one accountant-approved ``TaxTable`` version the same
  way.
* ``fx_rates`` stores one exchange-rate observation as an exact rational
  (``numerator / denominator``), never a float, with the source and fetch time.
* ``price_calculations`` stores one quote computation: the input request, the full
  ``PriceResult`` and the versions of the rules and tax table it used.

Immutability is deliberate. A correction is a newer version/observation, not an
edit, so an earlier quote keeps the data it actually saw. UPDATE/DELETE/TRUNCATE
are blocked by ORM listeners here and by the shared PostgreSQL trigger in
migration 0017.

Loading, validation and the write/read API are task 015c2; this module only
defines the schema.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import (
    CHAR,
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    event,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from travelos.core.db import BaseTable
from travelos.core.errors import AppendOnlyError

_CURRENCY_PATTERN = "^[A-Z]{3}$"
_SOURCE_HASH_PATTERN = "^[0-9a-f]{64}$"
_PRICE_STATUSES = "'ok', 'below_floor', 'uncompetitive', 'needs_config'"


class PricingRuleSetRecord(BaseTable):
    """One complete, owner-approved pricing rule set version."""

    __tablename__ = "pricing_rule_sets"

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    source_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    approved_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    active_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(f"source_hash ~ '{_SOURCE_HASH_PATTERN}'", name="source_hash_hex"),
        UniqueConstraint("tenant_id", "version", name="uq_pricing_rule_sets_version"),
        Index("ix_pricing_rule_sets_active", "tenant_id", "active_from"),
    )


class TaxRuleTableRecord(BaseTable):
    """One accountant-approved tax table version."""

    __tablename__ = "tax_rule_tables"

    version: Mapped[int] = mapped_column(Integer, nullable=False)
    rows: Mapped[list[dict[str, Any]]] = mapped_column(JSONB, nullable=False)
    source_hash: Mapped[str] = mapped_column(CHAR(64), nullable=False)
    approved_by_accountant: Mapped[bool] = mapped_column(nullable=False)
    approved_on: Mapped[date] = mapped_column(Date, nullable=False)
    approved_by: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    active_from: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint(f"source_hash ~ '{_SOURCE_HASH_PATTERN}'", name="source_hash_hex"),
        UniqueConstraint("tenant_id", "version", name="uq_tax_rule_tables_version"),
        Index("ix_tax_rule_tables_active", "tenant_id", "active_from"),
    )


class FxRateRecord(BaseTable):
    """One exchange-rate observation as an exact rational, never a float."""

    __tablename__ = "fx_rates"

    base_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    quote_currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    numerator: Mapped[int] = mapped_column(BigInteger, nullable=False)
    denominator: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint(f"base_currency ~ '{_CURRENCY_PATTERN}'", name="base_currency_code"),
        CheckConstraint(f"quote_currency ~ '{_CURRENCY_PATTERN}'", name="quote_currency_code"),
        CheckConstraint("numerator > 0", name="numerator_positive"),
        CheckConstraint("denominator > 0", name="denominator_positive"),
        Index("ix_fx_rates_pair", "tenant_id", "base_currency", "quote_currency", "fetched_at"),
    )


class PriceCalculationRecord(BaseTable):
    """One immutable quote computation and the versions it used.

    ``quote_revision_id`` is a nullable plain UUID with no ORM or database
    foreign key. The authoritative link for a quote is
    ``quote_revisions.price_calculation_id``; this reverse pointer is only a
    hint, filled when the caller already knows the revision id (which may not be
    inserted yet) and left null for a pricing preview.
    """

    __tablename__ = "price_calculations"

    quote_revision_id: Mapped[UUID | None] = mapped_column(PG_UUID(as_uuid=True))
    rule_set_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("pricing_rule_sets.id", ondelete="RESTRICT"),
        nullable=False,
    )
    tax_table_id: Mapped[UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("tax_rule_tables.id", ondelete="RESTRICT"), nullable=False
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    currency: Mapped[str] = mapped_column(CHAR(3), nullable=False)
    rule_version: Mapped[int] = mapped_column(Integer, nullable=False)
    tax_table_version: Mapped[int] = mapped_column(Integer, nullable=False)
    floor_paise: Mapped[int | None] = mapped_column(BigInteger)
    target_paise: Mapped[int | None] = mapped_column(BigInteger)
    ceiling_paise: Mapped[int | None] = mapped_column(BigInteger)
    recommended_paise: Mapped[int | None] = mapped_column(BigInteger)
    customer_price_paise: Mapped[int | None] = mapped_column(BigInteger)
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    outputs: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint(f"status IN ({_PRICE_STATUSES})", name="status"),
        CheckConstraint(f"currency ~ '{_CURRENCY_PATTERN}'", name="currency_code"),
        CheckConstraint("rule_version >= 1", name="rule_version_positive"),
        CheckConstraint("tax_table_version >= 1", name="tax_table_version_positive"),
        CheckConstraint("floor_paise IS NULL OR floor_paise >= 0", name="floor_non_negative"),
        CheckConstraint("target_paise IS NULL OR target_paise >= 0", name="target_non_negative"),
        CheckConstraint("ceiling_paise IS NULL OR ceiling_paise >= 0", name="ceiling_non_negative"),
        CheckConstraint(
            "recommended_paise IS NULL OR recommended_paise >= 0",
            name="recommended_non_negative",
        ),
        CheckConstraint(
            "customer_price_paise IS NULL OR customer_price_paise >= 0",
            name="customer_price_non_negative",
        ),
        CheckConstraint(
            "(status IN ('ok', 'below_floor')) = (customer_price_paise IS NOT NULL)",
            name="customer_price_matches_status",
        ),
        CheckConstraint(
            "(status IN ('ok', 'below_floor')) = (recommended_paise IS NOT NULL)",
            name="recommended_matches_status",
        ),
        CheckConstraint(
            "(status = 'needs_config') = (floor_paise IS NULL)",
            name="levels_missing_for_needs_config",
        ),
        CheckConstraint(
            "(status <> 'needs_config') = "
            "(floor_paise IS NOT NULL AND target_paise IS NOT NULL AND ceiling_paise IS NOT NULL)",
            name="levels_present_when_priced",
        ),
        Index("ix_price_calculations_created", "tenant_id", "created_at"),
        Index("ix_price_calculations_rule_set", "tenant_id", "rule_set_id"),
        Index("ix_price_calculations_tax_table", "tenant_id", "tax_table_id"),
        Index("ix_price_calculations_quote_revision", "quote_revision_id"),
    )


def _block_update(mapper: object, connection: object, target: object) -> None:
    del mapper, connection, target
    raise AppendOnlyError("pricing records are append-only: UPDATE is forbidden")


def _block_delete(mapper: object, connection: object, target: object) -> None:
    del mapper, connection, target
    raise AppendOnlyError("pricing records are append-only: DELETE is forbidden")


for _record in (PricingRuleSetRecord, TaxRuleTableRecord, FxRateRecord, PriceCalculationRecord):
    event.listen(_record, "before_update", _block_update)
    event.listen(_record, "before_delete", _block_delete)
