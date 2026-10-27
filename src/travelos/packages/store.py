"""Persist built itineraries and rebuild them without a live search (task 013b1).

The store writes the pure ``Itinerary`` as one ``itineraries`` row plus one
``itinerary_items`` row per chosen offer. A caller that knows which immutable
price snapshot an offer came from may pass ``snapshot_ids``; every mapping is
checked against the snapshot's supplier and offer id before it is stored, and an
unknown id is refused rather than silently dropped.

There is no pricing, tax, approval or booking behaviour here. The itinerary
records supplier cost only; the pricing engine (task 015) decides what a
customer pays, and ``book``/``cancel`` remain behind the booking orchestrator and
approval rules.
"""

from __future__ import annotations

from collections.abc import Mapping
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.core.audit import Actor, AuditWriter
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.packages.errors import ItineraryNotFoundError, PackagePersistenceError
from travelos.packages.models import (
    Itinerary,
    ItineraryComponent,
    ItineraryDay,
    OptionTier,
    RoomPlan,
    SelectionReason,
    TravellerParty,
)
from travelos.packages.records import ItineraryItemRecord, ItineraryRecord
from travelos.suppliers.models import Offer
from travelos.suppliers.records import PriceSnapshot, Supplier

_SnapshotKey = tuple[str, str]


class ItineraryStore:
    """Writes and reads itineraries for one tenant."""

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

    def record(
        self,
        itinerary: Itinerary,
        *,
        inquiry_id: UUID | None = None,
        option_tier: OptionTier | str = OptionTier.STANDARD,
        snapshot_ids: Mapping[_SnapshotKey, UUID] | None = None,
        actor: Actor | None = None,
    ) -> ItineraryRecord:
        """Store one itinerary and its items; the caller owns the transaction."""

        tier = _tier(option_tier)
        links = self._validated_snapshot_ids(itinerary, snapshot_ids)

        record = ItineraryRecord(
            tenant_id=self._tenant_id,
            inquiry_id=inquiry_id,
            option_tier=tier.value,
            trip_kind=itinerary.trip_kind.value,
            start_date=itinerary.start_date,
            end_date=itinerary.end_date,
            currency=itinerary.currency,
            total_cost_paise=itinerary.total_cost.paise,
            booked_rooms=itinerary.occupancy.booked_rooms,
            party=itinerary.party.model_dump(mode="json"),
            occupancy=itinerary.occupancy.model_dump(mode="json"),
            days=[day.model_dump(mode="json") for day in itinerary.days],
        )
        self._db.add(record)
        self._db.flush()

        for position, component in enumerate(itinerary.components):
            self._db.add(
                ItineraryItemRecord(
                    tenant_id=self._tenant_id,
                    itinerary_id=record.id,
                    position=position,
                    snapshot_id=links.get((component.offer.supplier, component.offer.offer_id)),
                    component_type=component.component_type.value,
                    supplier=component.offer.supplier,
                    offer_id=component.offer.offer_id,
                    quantity=component.quantity,
                    unit_basis=component.unit_basis.value,
                    start_date=component.start_date,
                    end_date=component.end_date,
                    day_index=component.day_index,
                    cost_paise=component.cost.paise,
                    currency=component.cost.currency,
                    reasons=[reason.value for reason in component.reasons],
                    explanation=component.explanation,
                    offer=component.offer.model_dump(mode="json"),
                )
            )
        self._db.flush()

        self._audit.record(
            self._db,
            action="packages.itinerary.recorded",
            actor=actor or Actor.system(),
            subject_type="itinerary",
            subject_id=record.id,
            after={
                "inquiry_id": str(inquiry_id) if inquiry_id is not None else None,
                "option_tier": record.option_tier,
                "total_cost_paise": record.total_cost_paise,
                "component_count": len(itinerary.components),
            },
        )
        return record

    def load(self, itinerary_id: UUID) -> Itinerary:
        """Rebuild a persisted itinerary, or fail if it is unknown/corrupted."""

        record = self._db.execute(
            select(ItineraryRecord).where(
                ItineraryRecord.id == itinerary_id,
                ItineraryRecord.tenant_id == self._tenant_id,
            )
        ).scalar_one_or_none()
        if record is None:
            raise ItineraryNotFoundError(f"itinerary {itinerary_id} is not recorded")

        items = list(
            self._db.execute(
                select(ItineraryItemRecord)
                .where(ItineraryItemRecord.itinerary_id == record.id)
                .order_by(ItineraryItemRecord.position)
            ).scalars()
        )
        components = tuple(
            ItineraryComponent(
                component_type=item.component_type,
                offer=Offer.model_validate(item.offer),
                quantity=item.quantity,
                unit_basis=item.unit_basis,
                start_date=item.start_date,
                end_date=item.end_date,
                day_index=item.day_index,
                reasons=tuple(SelectionReason(reason) for reason in item.reasons),
                explanation=item.explanation,
            )
            for item in items
        )
        itinerary = Itinerary(
            start_date=record.start_date,
            end_date=record.end_date,
            trip_kind=record.trip_kind,
            party=TravellerParty.model_validate(record.party),
            occupancy=RoomPlan.model_validate(record.occupancy),
            currency=record.currency,
            days=tuple(ItineraryDay.model_validate(day) for day in record.days),
            components=components,
        )
        if itinerary.total_cost.paise != record.total_cost_paise:
            raise PackagePersistenceError(f"itinerary {record.id} totals do not match its items")
        return itinerary

    def _validated_snapshot_ids(
        self,
        itinerary: Itinerary,
        snapshot_ids: Mapping[_SnapshotKey, UUID] | None,
    ) -> dict[_SnapshotKey, UUID]:
        if not snapshot_ids:
            return {}

        rows = self._db.execute(
            select(PriceSnapshot.id, PriceSnapshot.offer_id, Supplier.name)
            .join(Supplier, Supplier.id == PriceSnapshot.supplier_id)
            .where(
                PriceSnapshot.tenant_id == self._tenant_id,
                PriceSnapshot.id.in_(set(snapshot_ids.values())),
            )
        ).all()
        known_ids = {row[0] for row in rows}
        known_keys = {(row[2], row[1]): row[0] for row in rows}
        component_keys = {
            (component.offer.supplier, component.offer.offer_id)
            for component in itinerary.components
        }

        for key, snapshot_id in snapshot_ids.items():
            if key not in component_keys:
                raise PackagePersistenceError(
                    f"price snapshot {snapshot_id} is not in the itinerary as {key[0]} {key[1]}"
                )
            if snapshot_id not in known_ids:
                raise PackagePersistenceError(f"unknown price snapshot {snapshot_id}")
            if known_keys.get(key) != snapshot_id:
                raise PackagePersistenceError(
                    f"price snapshot {snapshot_id} does not match {key[0]} {key[1]}"
                )
        return dict(snapshot_ids)


def _tier(value: OptionTier | str) -> OptionTier:
    try:
        return OptionTier(value)
    except ValueError as exc:
        raise PackagePersistenceError(f"unknown option tier: {value!r}") from exc
