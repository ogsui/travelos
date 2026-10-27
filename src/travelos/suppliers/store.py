"""Supplier registration and the immutable snapshot store (task 012a2).

The store is deliberately small: it records offers and returns an unexpired
batch for the exact request hash. It never edits an old snapshot and never
interprets a fare rule. Caching a search is a read of supplier prices, not a
price calculation.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, timedelta
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from travelos.core.audit import Actor, AuditWriter
from travelos.core.clock import Clock, SystemClock
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.suppliers.errors import SupplierValidationError
from travelos.suppliers.models import Offer
from travelos.suppliers.records import PriceSnapshot, Supplier

_HEX_64 = re.compile(r"^[0-9a-f]{64}$")


def _validated_hash(request_hash: str) -> str:
    if not isinstance(request_hash, str) or not _HEX_64.fullmatch(request_hash):
        raise SupplierValidationError("request_hash must be a 64-character lowercase sha256 hex")
    return request_hash


class SupplierCatalog:
    """Resolves an adapter name to the one ``suppliers`` row the tenant owns."""

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

    def ensure(self, adapter: object) -> Supplier:
        """Return the supplier row for an adapter, registering it once.

        Registering is a small, audited state change. An adapter class change on
        an existing supplier is refused rather than silently swapped.
        """

        name = getattr(adapter, "name", None)
        if not isinstance(name, str) or not name.strip():
            raise SupplierValidationError("a supplier adapter needs a non-empty name")
        name = name.strip()
        adapter_name = type(adapter).__name__

        existing = self.by_name(name)
        if existing is not None:
            if existing.adapter != adapter_name:
                raise SupplierValidationError(
                    f"supplier {name!r} is registered with adapter {existing.adapter!r}, "
                    f"not {adapter_name!r}"
                )
            return existing

        supplier = Supplier(
            tenant_id=self._tenant_id,
            name=name,
            type="mixed",
            adapter=adapter_name,
            status="active",
        )
        self._db.add(supplier)
        self._db.flush()
        self._audit.record(
            self._db,
            action="supplier.registered",
            actor=Actor.system(),
            subject_type="supplier",
            subject_id=supplier.id,
            after={"name": supplier.name, "adapter": supplier.adapter},
        )
        return supplier

    def by_name(self, name: str) -> Supplier | None:
        return self._db.execute(
            select(Supplier).where(
                Supplier.tenant_id == self._tenant_id,
                Supplier.name == name,
            )
        ).scalar_one_or_none()


class SnapshotStore:
    """Records immutable offers and reads back a fresh batch for a request."""

    def __init__(
        self,
        db: Session,
        *,
        clock: Clock | None = None,
        ttl: timedelta = timedelta(minutes=10),
        tenant_id: UUID = DEFAULT_TENANT_ID,
    ) -> None:
        if not isinstance(ttl, timedelta) or ttl <= timedelta(0):
            raise SupplierValidationError("snapshot ttl must be a positive timedelta")
        self._db = db
        self._clock = clock or SystemClock()
        self._ttl = ttl
        self._tenant_id = tenant_id

    def record(
        self,
        *,
        supplier: Supplier,
        search_id: UUID | None,
        request_hash: str,
        offers: Iterable[Offer],
    ) -> tuple[PriceSnapshot, ...]:
        """Insert one row per offer. Returns the inserted rows in input order."""

        request_hash = _validated_hash(request_hash)
        if supplier.tenant_id != self._tenant_id:
            raise SupplierValidationError("supplier belongs to another tenant")

        rows: list[PriceSnapshot] = []
        for offer in offers:
            if offer.supplier != supplier.name:
                raise SupplierValidationError(
                    f"offer {offer.offer_id} belongs to {offer.supplier}, not {supplier.name}"
                )
            expires_at = min(offer.expires_at, offer.fetched_at + self._ttl)
            payload = offer.model_copy(update={"expires_at": expires_at}).model_dump(mode="json")
            row = PriceSnapshot(
                tenant_id=self._tenant_id,
                supplier_id=supplier.id,
                search_id=search_id,
                offer_id=offer.offer_id,
                product_type=offer.product_type,
                payload=payload,
                net_paise=offer.net_paise,
                taxes_paise=offer.taxes_paise,
                currency=offer.currency,
                fare_rules=offer.fare_rules.to_dict(),
                fetched_at=offer.fetched_at,
                expires_at=expires_at,
                request_hash=request_hash,
            )
            self._db.add(row)
            rows.append(row)

        if rows:
            self._db.flush()
        return tuple(rows)

    def fresh(self, *, supplier: Supplier, request_hash: str) -> tuple[Offer, ...] | None:
        """Return the latest batch for this query if none of it has expired."""

        request_hash = _validated_hash(request_hash)
        if supplier.tenant_id != self._tenant_id:
            raise SupplierValidationError("supplier belongs to another tenant")

        latest = self._db.execute(
            select(func.max(PriceSnapshot.fetched_at)).where(
                PriceSnapshot.tenant_id == self._tenant_id,
                PriceSnapshot.supplier_id == supplier.id,
                PriceSnapshot.request_hash == request_hash,
            )
        ).scalar_one_or_none()
        if latest is None:
            return None

        rows = (
            self._db.execute(
                select(PriceSnapshot)
                .where(
                    PriceSnapshot.tenant_id == self._tenant_id,
                    PriceSnapshot.supplier_id == supplier.id,
                    PriceSnapshot.request_hash == request_hash,
                    PriceSnapshot.fetched_at == latest,
                )
                .order_by(PriceSnapshot.offer_id, PriceSnapshot.id)
            )
            .scalars()
            .all()
        )
        now = self._clock.now().astimezone(UTC)
        if not rows or any(row.expires_at <= now for row in rows):
            return None
        return tuple(Offer.model_validate(row.payload) for row in rows)
