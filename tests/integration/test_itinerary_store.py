"""Persisted itineraries and their immutable offer provenance (task 013b1).

The store writes the pure itinerary as rows and can rebuild it byte-for-byte, so
a later pricing or quote step reads the same components that were explained to
the agent. A snapshot id is recorded only when it really names that supplier's
offer; an expired or swapped snapshot must not silently attach to a component.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select

from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.models import AuditLog
from travelos.packages.builder import build_package
from travelos.packages.errors import ItineraryNotFoundError, PackagePersistenceError
from travelos.packages.models import Itinerary, OptionTier
from travelos.packages.records import ItineraryItemRecord
from travelos.packages.store import ItineraryStore
from travelos.suppliers.models import CancellationRules, FareRules, Offer
from travelos.suppliers.records import PriceSnapshot, Supplier

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
START = date(2027, 2, 10)
END = date(2027, 2, 13)


def sample_requirement():
    from travelos.inquiry.requirement import Requirement

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
        sample_offer(
            offer_id="air-back",
            name="MockAir Goa to Delhi",
            route="Goa-Delhi",
        ),
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
    assert build.ok is True
    assert build.itinerary is not None
    return build.itinerary


def make_snapshot(
    db,
    offer: Offer,
    *,
    offer_id: str | None = None,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> PriceSnapshot:
    supplier = db.execute(
        select(Supplier).where(
            Supplier.tenant_id == tenant_id,
            Supplier.name == offer.supplier,
        )
    ).scalar_one_or_none()
    if supplier is None:
        supplier = Supplier(
            tenant_id=tenant_id,
            name=offer.supplier,
            type="mixed",
            adapter="SupplierSimulator",
        )
        db.add(supplier)
        db.flush()
    snapshot = PriceSnapshot(
        tenant_id=tenant_id,
        supplier_id=supplier.id,
        offer_id=offer_id or offer.offer_id,
        product_type=offer.product_type,
        payload=offer.model_dump(mode="json"),
        net_paise=offer.net_paise,
        taxes_paise=offer.taxes_paise,
        currency=offer.currency,
        fare_rules=offer.fare_rules.model_dump(mode="json"),
        fetched_at=offer.fetched_at,
        expires_at=offer.expires_at,
        request_hash="a" * 64,
    )
    db.add(snapshot)
    db.flush()
    return snapshot


def test_records_and_reloads_the_same_itinerary(db) -> None:
    itinerary = sample_itinerary()
    store = ItineraryStore(db)

    record = store.record(itinerary, option_tier=OptionTier.VALUE)

    assert record.option_tier == "value"
    assert record.total_cost_paise == itinerary.total_cost.paise == 4_700_000
    assert record.booked_rooms == 1

    loaded = store.load(record.id)
    assert loaded == itinerary

    items = list(
        db.execute(
            select(ItineraryItemRecord).where(ItineraryItemRecord.itinerary_id == record.id)
        ).scalars()
    )
    assert len(items) == len(itinerary.components) == 4
    assert sum(item.cost_paise for item in items) == itinerary.total_cost.paise
    assert all(item.snapshot_id is None for item in items)


def test_recording_writes_one_audit_entry(db) -> None:
    itinerary = sample_itinerary()
    record = ItineraryStore(db).record(itinerary)
    db.flush()

    entries = list(
        db.execute(
            select(AuditLog).where(AuditLog.action == "packages.itinerary.recorded")
        ).scalars()
    )

    assert len(entries) == 1
    assert entries[0].subject_type == "itinerary"
    assert entries[0].subject_id == record.id
    assert entries[0].after["total_cost_paise"] == itinerary.total_cost.paise
    assert entries[0].after["option_tier"] == "standard"


def test_items_link_only_to_the_matching_supplier_snapshot(db) -> None:
    itinerary = sample_itinerary()
    outbound = next(
        component.offer
        for component in itinerary.components
        if component.offer.offer_id == "air-out"
    )
    snapshot = make_snapshot(db, outbound)

    record = ItineraryStore(db).record(
        itinerary,
        snapshot_ids={("MockAir", "air-out"): snapshot.id},
    )

    items = {
        item.offer_id: item
        for item in db.execute(
            select(ItineraryItemRecord).where(ItineraryItemRecord.itinerary_id == record.id)
        ).scalars()
    }
    assert items["air-out"].snapshot_id == snapshot.id
    assert all(
        item.snapshot_id is None for offer_id, item in items.items() if offer_id != "air-out"
    )


def test_a_snapshot_for_another_offer_is_refused(db) -> None:
    itinerary = sample_itinerary()
    wrong = next(
        component.offer
        for component in itinerary.components
        if component.offer.offer_id == "air-back"
    )
    snapshot = make_snapshot(db, wrong)

    with pytest.raises(PackagePersistenceError, match="does not match"):
        ItineraryStore(db).record(
            itinerary,
            snapshot_ids={("MockAir", "air-out"): snapshot.id},
        )


def test_an_unknown_snapshot_is_refused(db) -> None:
    with pytest.raises(PackagePersistenceError, match="unknown price snapshot"):
        ItineraryStore(db).record(
            sample_itinerary(),
            snapshot_ids={("MockAir", "air-out"): uuid4()},
        )


def test_a_mapping_for_a_component_outside_the_itinerary_is_refused(db) -> None:
    itinerary = sample_itinerary()
    other = sample_offer(offer_id="air-other", route="Delhi-Goa")
    snapshot = make_snapshot(db, other)

    with pytest.raises(PackagePersistenceError, match="not in the itinerary"):
        ItineraryStore(db).record(
            itinerary,
            snapshot_ids={("MockAir", "air-other"): snapshot.id},
        )


def test_an_unknown_option_tier_is_refused(db) -> None:
    with pytest.raises(PackagePersistenceError, match="option tier"):
        ItineraryStore(db).record(sample_itinerary(), option_tier="gold")


def test_another_tenant_cannot_load_the_itinerary(db) -> None:
    record = ItineraryStore(db, tenant_id=uuid4()).record(sample_itinerary())

    with pytest.raises(ItineraryNotFoundError):
        ItineraryStore(db).load(record.id)
