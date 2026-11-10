"""Build a customer-safe quote document from stored rows (task 016b2b).

This is the only place that turns persisted quote revisions, itineraries and the
owner's presentation rules into the :class:`QuoteDocument` the renderer accepts.
It copies the stored total and the stored ``tax`` lines only: supplier cost,
margin, gateway, rounding and proposal lines never leave this module.

Nothing is invented. Prices come from the revision, item names and fare
conditions from the immutable offer stored on the itinerary, labels and titles
from staff, and brand/terms/payment wording from ``quote_presentation``. A
missing traveller count, component order or stored itinerary is refused rather
than rendered.

The caller owns the transaction; this module only reads.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.core.db import DEFAULT_TENANT_ID
from travelos.packages.models import ItineraryComponent
from travelos.packages.records import ItineraryItemRecord
from travelos.packages.store import ItineraryStore
from travelos.quotes.errors import QuoteValidationError
from travelos.quotes.groups import QuoteGroupStore
from travelos.quotes.models import LineKind
from travelos.quotes.presentation import QuotePresentation
from travelos.quotes.records import QuoteRecord
from travelos.quotes.render import DayView, FareRuleView, ItemView, OptionView, QuoteDocument
from travelos.quotes.store import QuoteStore

DEFAULT_VISA_NOTE = "To be confirmed"
# Defensive bounds for the view models; a larger group needs an owner decision.
MAX_TRAVELLERS = 200
MAX_QUANTITY = 200


def build_quote_document(
    db: Session,
    quote_id: UUID,
    *,
    presentation: QuotePresentation,
    now: datetime,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> QuoteDocument:
    """Build the document for one quote's current revision."""

    quote = QuoteStore(db, tenant_id=tenant_id).load(quote_id).quote
    option = _option(db, quote, label=None, tenant_id=tenant_id)
    return _document(
        document_id=quote.id,
        kind="quote",
        title=None,
        options=(option,),
        prepared_at=now,
        expires_at=quote.expires_at,
        presentation=presentation,
    )


def build_group_document(
    db: Session,
    group_id: UUID,
    *,
    presentation: QuotePresentation,
    now: datetime,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> QuoteDocument:
    """Build a multi-option document in the staff order they grouped it."""

    view = QuoteGroupStore(db, tenant_id=tenant_id).load(group_id)
    options = tuple(
        _option(db, quote, label=record.label, tenant_id=tenant_id)
        for record, quote in zip(view.options, view.quotes, strict=True)
    )
    return _document(
        document_id=view.group.id,
        kind="group",
        title=view.group.title,
        options=options,
        prepared_at=now,
        # The document is valid only while every option is: use the first expiry.
        expires_at=min(quote.expires_at for quote in view.quotes),
        presentation=presentation,
    )


def _document(
    *,
    document_id: UUID,
    kind: Literal["quote", "group"],
    title: str | None,
    options: tuple[OptionView, ...],
    prepared_at: datetime,
    expires_at: datetime,
    presentation: QuotePresentation,
) -> QuoteDocument:
    return QuoteDocument(
        id=document_id,
        kind=kind,
        title=title,
        prepared_at=_aware(prepared_at, "now"),
        expires_at=_aware(expires_at, "quote expiry"),
        timezone=presentation.timezone,
        brand=presentation.brand,
        options=options,
        payment_schedule=presentation.payment_schedule,
        terms=presentation.terms,
        visa_note=presentation.visa.source_note or DEFAULT_VISA_NOTE,
    )


def _option(
    db: Session,
    quote: QuoteRecord,
    *,
    label: str | None,
    tenant_id: UUID,
) -> OptionView:
    revision = QuoteStore(db, tenant_id=tenant_id).load(quote.id).current
    itinerary = ItineraryStore(db, tenant_id=tenant_id).load(revision.itinerary_id)
    snapshots = _snapshot_ids(db, revision.itinerary_id)
    ordered = tuple(component for day in itinerary.days for component in day.components)

    if ordered != itinerary.components or len(snapshots) != len(ordered):
        raise QuoteValidationError(
            f"itinerary {revision.itinerary_id} does not match its stored components"
        )
    travellers = itinerary.party.travellers
    if not 1 <= travellers <= MAX_TRAVELLERS:
        raise QuoteValidationError(
            f"itinerary {revision.itinerary_id} has {travellers} travellers, not 1-{MAX_TRAVELLERS}"
        )

    position = 0
    days: list[DayView] = []
    for day in itinerary.days:
        items = tuple(
            _item(component, snapshots[position + offset])
            for offset, component in enumerate(day.components)
        )
        position += len(day.components)
        days.append(DayView(day_index=day.day_index, date=day.date, items=items))

    return OptionView(
        label=label,
        quote_id=quote.id,
        revision_number=revision.number,
        total=revision.total,
        tax_lines=tuple(line for line in revision.lines if line.kind is LineKind.TAX),
        start_date=itinerary.start_date,
        end_date=itinerary.end_date,
        travellers=travellers,
        days=tuple(days),
        snapshot_refs=tuple(dict.fromkeys(str(ref) for ref in snapshots if ref is not None)),
    )


def _item(component: ItineraryComponent, snapshot_id: UUID | None) -> ItemView:
    offer = component.offer
    if not 1 <= component.quantity <= MAX_QUANTITY:
        raise QuoteValidationError(
            f"component {offer.supplier} {offer.offer_id} has {component.quantity} units, "
            f"not 1-{MAX_QUANTITY}"
        )
    return ItemView(
        component_type=component.component_type.value,
        name=offer.name,
        supplier=offer.supplier,
        city=offer.city,
        start_date=component.start_date,
        end_date=component.end_date,
        quantity=component.quantity,
        unit_basis=component.unit_basis.value,
        snapshot_ref=str(snapshot_id) if snapshot_id is not None else None,
        fare_rule=FareRuleView(
            product=component.component_type.value,
            refundable=offer.fare_rules.cancellation.refundable,
            cancellation_days=offer.fare_rules.cancellation.cancellation_days,
            change_fee=offer.fare_rules.change_fee,
        ),
    )


def _snapshot_ids(db: Session, itinerary_id: UUID) -> tuple[UUID | None, ...]:
    rows = db.execute(
        select(ItineraryItemRecord.snapshot_id)
        .where(ItineraryItemRecord.itinerary_id == itinerary_id)
        .order_by(ItineraryItemRecord.position)
    ).scalars()
    return tuple(rows)


def _aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise QuoteValidationError(f"{name} must be timezone-aware")
    return value.astimezone(UTC)


__all__ = ["DEFAULT_VISA_NOTE", "build_group_document", "build_quote_document"]
