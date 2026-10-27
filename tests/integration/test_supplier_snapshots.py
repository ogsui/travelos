"""Supplier registry, searches and immutable price snapshots (task 012a2)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from travelos.core.clock import ManualClock
from travelos.core.errors import AppendOnlyError
from travelos.suppliers.errors import SupplierValidationError
from travelos.suppliers.models import (
    CancellationRules,
    FareRules,
    Offer,
)
from travelos.suppliers.records import PriceSnapshot, Supplier
from travelos.suppliers.store import SnapshotStore, SupplierCatalog

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
REQUEST_HASH = "a" * 64


def make_offer(
    *,
    offer_id: str = "sku_0001",
    fetched_at: datetime = NOW,
    expires_at: datetime | None = None,
    net_paise: int = 3_000_000,
) -> Offer:
    return Offer(
        supplier="MockAir",
        offer_id=offer_id,
        product_type="flight",
        name="MockAir Mumbai to Goa",
        city="Goa",
        route="Mumbai-Goa",
        currency="INR",
        net_paise=net_paise,
        taxes_paise=150_000,
        available_count=8,
        fare_rules=FareRules(
            cancellation=CancellationRules(
                refundable=True,
                cancellation_days=7,
                change_fee_paise=0,
            )
        ),
        fetched_at=fetched_at,
        expires_at=expires_at or fetched_at + timedelta(hours=1),
    )


def add_supplier(db: Session, *, name: str = "MockAir", status: str = "active") -> Supplier:
    supplier = Supplier(
        name=name,
        type="flight",
        adapter="SupplierSimulator",
        status=status,
    )
    db.add(supplier)
    db.flush()
    return supplier


def store(
    db: Session,
    *,
    clock: ManualClock | None = None,
    ttl: timedelta = timedelta(minutes=10),
) -> SnapshotStore:
    return SnapshotStore(db, clock=clock or ManualClock(NOW), ttl=ttl)


def record(
    db: Session,
    *,
    supplier: Supplier,
    offers: tuple[Offer, ...] | None = None,
    request_hash: str = REQUEST_HASH,
) -> tuple[PriceSnapshot, ...]:
    return store(db).record(
        supplier=supplier,
        search_id=None,
        request_hash=request_hash,
        offers=offers or (make_offer(),),
    )


# ---------------------------------------------------------------------------
# Catalog
# ---------------------------------------------------------------------------


class _StubAdapter:
    name = "MockAir"

    def search(self, query: object) -> object:  # pragma: no cover - not called here
        raise AssertionError("the catalog never calls the adapter")


def test_catalog_registers_a_supplier_once(db: Session) -> None:
    catalog = SupplierCatalog(db)
    adapter = _StubAdapter()

    first = catalog.ensure(adapter)
    second = catalog.ensure(adapter)

    assert first.id == second.id
    assert first.name == "MockAir"
    assert first.adapter == "_StubAdapter"
    assert first.status == "active"
    rows = db.execute(select(Supplier)).scalars().all()
    assert len(rows) == 1


def test_catalog_refuses_to_silently_change_an_adapter(db: Session) -> None:
    catalog = SupplierCatalog(db)
    catalog.ensure(_StubAdapter())

    class OtherAdapter:
        name = "MockAir"

        def search(self, query: object) -> object:  # pragma: no cover
            raise AssertionError("not called")

    with pytest.raises(SupplierValidationError, match="adapter"):
        catalog.ensure(OtherAdapter())


# ---------------------------------------------------------------------------
# Snapshots
# ---------------------------------------------------------------------------


def test_snapshot_records_an_offer_as_immutable_exact_money(db: Session) -> None:
    supplier = add_supplier(db)
    rows = record(db, supplier=supplier)

    assert len(rows) == 1
    row = rows[0]
    assert row.supplier_id == supplier.id
    assert row.offer_id == "sku_0001"
    assert row.product_type == "flight"
    assert row.net_paise == 3_000_000
    assert row.taxes_paise == 150_000
    assert isinstance(row.net_paise, int)
    assert row.currency == "INR"
    assert row.request_hash == REQUEST_HASH
    assert row.fetched_at == NOW
    assert row.expires_at == NOW + timedelta(minutes=10)
    assert row.payload["offer_id"] == "sku_0001"
    assert row.payload["expires_at"] == "2026-11-01T10:10:00Z"
    assert row.fare_rules == {
        "refundable": True,
        "cancellation_days": 7,
        "change_fee_paise": 0,
        "currency": "INR",
    }


def test_snapshot_ttl_caps_a_longer_offer_expiry(db: Session) -> None:
    supplier = add_supplier(db)
    offer = make_offer(expires_at=NOW + timedelta(hours=6))

    row = record(db, supplier=supplier, offers=(offer,))[0]

    assert row.expires_at == NOW + timedelta(minutes=10)
    assert row.payload["expires_at"] == "2026-11-01T10:10:00Z"


def test_fresh_snapshots_are_reused_until_they_expire(db: Session) -> None:
    supplier = add_supplier(db)
    clock = ManualClock(NOW)
    snapshots = store(db, clock=clock)
    snapshots.record(
        supplier=supplier,
        search_id=None,
        request_hash=REQUEST_HASH,
        offers=(make_offer(),),
    )

    cached = snapshots.fresh(supplier=supplier, request_hash=REQUEST_HASH)
    assert cached is not None
    assert [offer.offer_id for offer in cached] == ["sku_0001"]
    assert cached[0].total_paise == 3_150_000

    clock.advance(timedelta(minutes=10))
    assert snapshots.fresh(supplier=supplier, request_hash=REQUEST_HASH) is None


def test_snapshots_are_scoped_by_request_hash_and_supplier(db: Session) -> None:
    supplier = add_supplier(db)
    other = add_supplier(db, name="MockHotels")
    snapshots = store(db)
    snapshots.record(
        supplier=supplier,
        search_id=None,
        request_hash=REQUEST_HASH,
        offers=(make_offer(),),
    )

    assert snapshots.fresh(supplier=supplier, request_hash="b" * 64) is None
    assert snapshots.fresh(supplier=other, request_hash=REQUEST_HASH) is None


def test_a_newer_snapshot_batch_wins_over_an_expired_one(db: Session) -> None:
    supplier = add_supplier(db)
    clock = ManualClock(NOW)
    snapshots = store(db, clock=clock)
    snapshots.record(
        supplier=supplier,
        search_id=None,
        request_hash=REQUEST_HASH,
        offers=(make_offer(net_paise=3_000_000),),
    )

    clock.advance(timedelta(minutes=11))
    snapshots.record(
        supplier=supplier,
        search_id=None,
        request_hash=REQUEST_HASH,
        offers=(make_offer(fetched_at=clock.now(), net_paise=3_500_000),),
    )

    cached = snapshots.fresh(supplier=supplier, request_hash=REQUEST_HASH)
    assert cached is not None
    assert cached[0].net_paise == 3_500_000


def test_snapshot_rejects_an_empty_or_bad_request_hash(db: Session) -> None:
    supplier = add_supplier(db)

    with pytest.raises(SupplierValidationError, match="request_hash"):
        store(db).record(
            supplier=supplier,
            search_id=None,
            request_hash="not-a-hash",
            offers=(make_offer(),),
        )


def test_orm_cannot_update_a_snapshot(db: Session) -> None:
    supplier = add_supplier(db)
    row = record(db, supplier=supplier)[0]

    row.net_paise = 1
    with pytest.raises(AppendOnlyError, match="price_snapshots"):
        db.flush()


def test_orm_cannot_delete_a_snapshot(db: Session) -> None:
    supplier = add_supplier(db)
    row = record(db, supplier=supplier)[0]

    db.delete(row)
    with pytest.raises(AppendOnlyError, match="price_snapshots"):
        db.flush()


def test_sql_cannot_update_a_snapshot(db: Session) -> None:
    supplier = add_supplier(db)
    row = record(db, supplier=supplier)[0]

    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(
            text("UPDATE price_snapshots SET net_paise = 1 WHERE id = :id"),
            {"id": row.id},
        )


def test_sql_cannot_delete_a_snapshot(db: Session) -> None:
    supplier = add_supplier(db)
    row = record(db, supplier=supplier)[0]

    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(
            text("DELETE FROM price_snapshots WHERE id = :id"),
            {"id": row.id},
        )
