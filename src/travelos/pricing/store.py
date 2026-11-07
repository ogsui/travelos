"""Persist and read pricing policy, FX observations and calculations (015c2).

The store is the only writer for the four append-only pricing tables. It turns a
complete, validated domain object into one immutable version and back, and it
refuses anything that would make a stored price unattributable:

* a rule set or tax table is hashed from its canonical JSON, and the hash is
  re-checked on every read, so a payload edited behind the trigger's back is
  detected rather than used;
* a tax table is only stored when the accountant approved it;
* a calculation may only cite an existing rule set and tax table whose versions
  match the request and the result it records;
* an FX observation keeps its exact rational rate, and ``latest_fx`` returns the
  newest observation at or before the caller's moment (never a future one).

There is no clock and no network here. Callers pass ``now`` and ``fetched_at`` so
a calculation can be reproduced from the data it cites. Quote wiring and the
approval routing are later slices (016 and 015d).
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.core.audit import Actor, AuditWriter
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.money import Money
from travelos.pricing.arithmetic import FxSnapshot
from travelos.pricing.calculator import PriceRequest, PriceResult
from travelos.pricing.errors import PricingValidationError
from travelos.pricing.models import PricingRuleSet
from travelos.pricing.records import (
    FxRateRecord,
    PriceCalculationRecord,
    PricingRuleSetRecord,
    TaxRuleTableRecord,
)
from travelos.pricing.tax import TaxTable


def canonical_hash(payload: Mapping[str, Any]) -> str:
    """Return the lowercase sha256 of a payload's canonical JSON form."""

    encoded = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=True,
    )
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class StoredRuleSet:
    """One verified rule-set version, with the row that stores it."""

    id: UUID
    version: int
    rules: PricingRuleSet
    source_hash: str
    approved_by: UUID
    approved_at: datetime
    active_from: datetime


@dataclass(frozen=True, slots=True)
class StoredTaxTable:
    """One verified, accountant-approved tax-table version."""

    id: UUID
    version: int
    table: TaxTable
    source_hash: str
    approved_by: UUID
    active_from: datetime


class PricingStore:
    """Writes and reads pricing records for one tenant."""

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
    # Rule sets
    # ------------------------------------------------------------------

    def save_rule_set(
        self,
        rules: PricingRuleSet,
        *,
        approved_by: UUID,
        approved_at: datetime,
        active_from: datetime,
        actor: Actor | None = None,
    ) -> StoredRuleSet:
        """Store one complete rule-set version; the caller owns the transaction.

        The hash covers the canonical JSON of the complete rules payload, so a
        later read can prove the rules it hands to the calculator are the ones
        that were approved.
        """

        payload = rules.model_dump(mode="json")
        approved_at = _moment(approved_at, "approved_at")
        active_from = _moment(active_from, "active_from")
        self._require_free_version(PricingRuleSetRecord, rules.version, "pricing rule set")

        row = PricingRuleSetRecord(
            tenant_id=self._tenant_id,
            version=rules.version,
            rules=payload,
            source_hash=canonical_hash(payload),
            approved_by=approved_by,
            approved_at=approved_at,
            active_from=active_from,
        )
        self._db.add(row)
        self._db.flush()
        self._audit.record(
            self._db,
            action="pricing.ruleset.saved",
            actor=actor or Actor.system(),
            subject_type="pricing_rule_set",
            subject_id=row.id,
            after={
                "version": row.version,
                "source_hash": row.source_hash,
                "approved_by": str(row.approved_by),
                "active_from": row.active_from.isoformat(),
            },
        )
        return _stored_rule_set(row)

    def active_rule_set(self, *, now: datetime) -> StoredRuleSet | None:
        """The highest rule-set version already in force at ``now``, if any."""

        moment = _moment(now, "now")
        row = (
            self._db.execute(
                select(PricingRuleSetRecord)
                .where(
                    PricingRuleSetRecord.tenant_id == self._tenant_id,
                    PricingRuleSetRecord.active_from <= moment,
                )
                .order_by(
                    PricingRuleSetRecord.version.desc(),
                    PricingRuleSetRecord.active_from.desc(),
                    PricingRuleSetRecord.id.desc(),
                )
                .limit(1)
            )
            .scalars()
            .first()
        )
        return None if row is None else _stored_rule_set(row)

    # ------------------------------------------------------------------
    # Tax tables
    # ------------------------------------------------------------------

    def save_tax_table(
        self,
        table: TaxTable,
        *,
        approved_by: UUID,
        active_from: datetime,
        actor: Actor | None = None,
    ) -> StoredTaxTable:
        """Store one accountant-approved tax-table version.

        ``approved_by_accountant`` is a domain fact, not a flag the store may
        assume: an unapproved table is refused instead of being stored and later
        rejected by the calculator.
        """

        if not table.approved_by_accountant:
            raise PricingValidationError(
                f"tax table {table.version} is not approved by the accountant"
            )
        payload = table.model_dump(mode="json")
        active_from = _moment(active_from, "active_from")
        self._require_free_version(TaxRuleTableRecord, table.version, "tax table")

        row = TaxRuleTableRecord(
            tenant_id=self._tenant_id,
            version=table.version,
            rows=payload["rows"],
            source_hash=canonical_hash(payload),
            approved_by_accountant=True,
            approved_on=table.approved_on,
            approved_by=approved_by,
            active_from=active_from,
        )
        self._db.add(row)
        self._db.flush()
        self._audit.record(
            self._db,
            action="pricing.tax_table.saved",
            actor=actor or Actor.system(),
            subject_type="tax_rule_table",
            subject_id=row.id,
            after={
                "version": row.version,
                "source_hash": row.source_hash,
                "approved_on": row.approved_on.isoformat(),
                "approved_by": str(row.approved_by),
                "active_from": row.active_from.isoformat(),
            },
        )
        return _stored_tax_table(row)

    def active_tax_table(self, *, now: datetime) -> StoredTaxTable | None:
        """The highest tax-table version already in force at ``now``, if any."""

        moment = _moment(now, "now")
        row = (
            self._db.execute(
                select(TaxRuleTableRecord)
                .where(
                    TaxRuleTableRecord.tenant_id == self._tenant_id,
                    TaxRuleTableRecord.active_from <= moment,
                )
                .order_by(
                    TaxRuleTableRecord.version.desc(),
                    TaxRuleTableRecord.active_from.desc(),
                    TaxRuleTableRecord.id.desc(),
                )
                .limit(1)
            )
            .scalars()
            .first()
        )
        return None if row is None else _stored_tax_table(row)

    # ------------------------------------------------------------------
    # FX observations
    # ------------------------------------------------------------------

    def record_fx(self, snapshot: FxSnapshot, *, actor: Actor | None = None) -> FxRateRecord:
        """Store one exact FX observation. A newer observation supersedes it."""

        row = FxRateRecord(
            tenant_id=self._tenant_id,
            base_currency=snapshot.base_currency,
            quote_currency=snapshot.quote_currency,
            numerator=snapshot.numerator,
            denominator=snapshot.denominator,
            source=snapshot.source,
            fetched_at=_moment(snapshot.fetched_at, "fetched_at"),
        )
        self._db.add(row)
        self._db.flush()
        self._audit.record(
            self._db,
            action="pricing.fx.recorded",
            actor=actor or Actor.system(),
            subject_type="fx_rate",
            subject_id=row.id,
            after={
                "base_currency": row.base_currency,
                "quote_currency": row.quote_currency,
                "numerator": row.numerator,
                "denominator": row.denominator,
                "fetched_at": row.fetched_at.isoformat(),
            },
        )
        return row

    def latest_fx(
        self,
        base_currency: str,
        quote_currency: str,
        *,
        now: datetime,
        max_age: timedelta | None = None,
    ) -> FxSnapshot | None:
        """Newest observation for the pair at or before ``now``, if any.

        ``max_age`` is optional because the owner has not set a freshness rule
        yet; when supplied, an older observation is treated as unavailable rather
        than silently used. A missing rate is never converted as 1:1.
        """

        moment = _moment(now, "now")
        if max_age is not None and (not isinstance(max_age, timedelta) or max_age <= timedelta(0)):
            raise PricingValidationError("max_age must be a positive timedelta")

        conditions = [
            FxRateRecord.tenant_id == self._tenant_id,
            FxRateRecord.base_currency == base_currency,
            FxRateRecord.quote_currency == quote_currency,
            FxRateRecord.fetched_at <= moment,
        ]
        if max_age is not None:
            conditions.append(FxRateRecord.fetched_at >= moment - max_age)

        row = (
            self._db.execute(
                select(FxRateRecord)
                .where(*conditions)
                .order_by(
                    FxRateRecord.fetched_at.desc(),
                    FxRateRecord.created_at.desc(),
                    FxRateRecord.id.desc(),
                )
                .limit(1)
            )
            .scalars()
            .first()
        )
        if row is None:
            return None
        return FxSnapshot(
            base_currency=row.base_currency,
            quote_currency=row.quote_currency,
            numerator=row.numerator,
            denominator=row.denominator,
            fetched_at=row.fetched_at,
            source=row.source,
        )

    # ------------------------------------------------------------------
    # Price calculations
    # ------------------------------------------------------------------

    def record_calculation(
        self,
        request: PriceRequest,
        result: PriceResult,
        *,
        rule_set_id: UUID,
        tax_table_id: UUID,
        quote_revision_id: UUID | None = None,
        actor: Actor | None = None,
    ) -> PriceCalculationRecord:
        """Store one immutable calculation and the versions it cites.

        The cited rows are re-verified, and their versions must equal both the
        request and the result. That prevents a price from being filed against a
        policy version that did not produce it.
        """

        rules_row = self._db.get(PricingRuleSetRecord, rule_set_id)
        if rules_row is None or rules_row.tenant_id != self._tenant_id:
            raise PricingValidationError(f"pricing rule set {rule_set_id} was not found")
        tax_row = self._db.get(TaxRuleTableRecord, tax_table_id)
        if tax_row is None or tax_row.tenant_id != self._tenant_id:
            raise PricingValidationError(f"tax table {tax_table_id} was not found")

        rules = _stored_rule_set(rules_row)
        table = _stored_tax_table(tax_row)

        if request.rules.version != rules.version or result.rule_version != rules.version:
            raise PricingValidationError(
                f"rule set version {request.rules.version} does not match the cited "
                f"version {rules.version}"
            )
        if request.rules.tax_table_version != table.version:
            raise PricingValidationError(
                f"tax table version {request.rules.tax_table_version} does not match the "
                f"cited version {table.version}"
            )
        if request.tax_table.version != table.version or result.tax_table_version != table.version:
            raise PricingValidationError(
                f"tax table version {request.tax_table.version} does not match the cited "
                f"version {table.version}"
            )

        row = PriceCalculationRecord(
            tenant_id=self._tenant_id,
            quote_revision_id=quote_revision_id,
            rule_set_id=rules.id,
            tax_table_id=table.id,
            status=result.status.value,
            currency=result.currency,
            rule_version=rules.version,
            tax_table_version=table.version,
            floor_paise=_paise(result.floor),
            target_paise=_paise(result.target),
            ceiling_paise=_paise(result.ceiling),
            recommended_paise=_paise(result.recommended),
            customer_price_paise=_paise(result.customer_price),
            inputs=_calculation_inputs(request),
            outputs=result.model_dump(mode="json"),
        )
        self._db.add(row)
        self._db.flush()
        self._audit.record(
            self._db,
            action="pricing.calculation.recorded",
            actor=actor or Actor.system(),
            subject_type="price_calculation",
            subject_id=row.id,
            after={
                "status": row.status,
                "currency": row.currency,
                "customer_price_paise": row.customer_price_paise,
                "rule_version": row.rule_version,
                "tax_table_version": row.tax_table_version,
                "quote_revision_id": (
                    str(row.quote_revision_id) if row.quote_revision_id is not None else None
                ),
            },
        )
        return row

    def get_calculation(self, calculation_id: UUID) -> PriceCalculationRecord | None:
        """Read one stored calculation for this tenant, if it exists."""

        return (
            self._db.execute(
                select(PriceCalculationRecord).where(
                    PriceCalculationRecord.tenant_id == self._tenant_id,
                    PriceCalculationRecord.id == calculation_id,
                )
            )
            .scalars()
            .first()
        )

    # ------------------------------------------------------------------

    def _require_free_version(
        self,
        record: type[PricingRuleSetRecord | TaxRuleTableRecord],
        version: int,
        label: str,
    ) -> None:
        existing = self._db.execute(
            select(record.id).where(
                record.tenant_id == self._tenant_id,
                record.version == version,
            )
        ).first()
        if existing is not None:
            raise PricingValidationError(f"{label} version {version} already exists")


def _stored_rule_set(row: PricingRuleSetRecord) -> StoredRuleSet:
    if canonical_hash(row.rules) != row.source_hash:
        raise PricingValidationError(
            f"stored pricing rule set {row.version} hash does not match its payload"
        )
    try:
        rules = PricingRuleSet.model_validate(row.rules)
    except PydanticValidationError as exc:
        raise PricingValidationError(
            f"stored pricing rule set {row.version} is invalid: {exc}"
        ) from None
    if rules.version != row.version:
        raise PricingValidationError(
            f"stored pricing rule set {row.version} version does not match its payload"
        )
    return StoredRuleSet(
        id=row.id,
        version=row.version,
        rules=rules,
        source_hash=row.source_hash,
        approved_by=row.approved_by,
        approved_at=row.approved_at,
        active_from=row.active_from,
    )


def _stored_tax_table(row: TaxRuleTableRecord) -> StoredTaxTable:
    payload: dict[str, Any] = {
        "version": row.version,
        "currency": "INR",
        "approved_by_accountant": row.approved_by_accountant,
        "approved_on": row.approved_on.isoformat(),
        "rows": row.rows,
    }
    if canonical_hash(payload) != row.source_hash:
        raise PricingValidationError(
            f"stored tax table {row.version} hash does not match its payload"
        )
    try:
        table = TaxTable.model_validate(payload)
    except PydanticValidationError as exc:
        raise PricingValidationError(f"stored tax table {row.version} is invalid: {exc}") from None
    if not table.approved_by_accountant:
        raise PricingValidationError(
            f"stored tax table {row.version} is not approved by the accountant"
        )
    if table.version != row.version:
        raise PricingValidationError(
            f"stored tax table {row.version} version does not match its payload"
        )
    return StoredTaxTable(
        id=row.id,
        version=row.version,
        table=table,
        source_hash=row.source_hash,
        approved_by=row.approved_by,
        active_from=row.active_from,
    )


def _calculation_inputs(request: PriceRequest) -> dict[str, Any]:
    """The reproducible request without duplicating the cited policy payloads."""

    return {
        "items": [item.model_dump(mode="json") for item in request.items],
        "segment": request.segment.value,
        "gateway_fee": request.gateway_fee.to_dict(),
        "competitor_reference": _money_dict(request.competitor_reference),
        "proposed_price": _money_dict(request.proposed_price),
        "fx_snapshots": [snapshot.model_dump(mode="json") for snapshot in request.fx_snapshots],
        "now": request.now.isoformat(),
    }


def _money_dict(amount: Money | None) -> dict[str, int | str] | None:
    return None if amount is None else amount.to_dict()


def _paise(amount: Money | None) -> int | None:
    return None if amount is None else amount.paise


def _moment(value: datetime, name: str) -> datetime:
    if not isinstance(value, datetime):
        raise PricingValidationError(f"{name} must be a datetime")
    if value.tzinfo is None or value.utcoffset() is None:
        raise PricingValidationError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


__all__ = [
    "PricingStore",
    "StoredRuleSet",
    "StoredTaxTable",
    "canonical_hash",
]
