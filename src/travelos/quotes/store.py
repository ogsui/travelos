"""Create and read quote revisions from stored prices (task 016a).

The store is deliberately narrow. It does not calculate a price, choose an
itinerary, render a document, send anything or decide an approval. It copies one
price calculation and one persisted itinerary into an immutable revision, checks
that the lines add up to the calculation's customer total exactly, and stores the
revision diff.

A calculation whose status is not ``ok`` is refused unless the caller cites an
owner price override (task 016c2c) that authorised exactly that calculation and
total. The override is stored on the revision, so every customer-facing price can
be traced either to an ``ok`` calculation or to a recorded owner decision.

Only ``DRAFT`` quotes accept another revision in this slice. Re-quoting a sent
quote needs the customer-change workflow and is task 016d; silently replacing an
approved revision would hide the change from the reviewer.

Every write is tenant-scoped and audited. The caller owns the transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.core.audit import Actor, AuditWriter
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.errors import ValidationError as DomainValidationError
from travelos.core.ids import uuid7
from travelos.core.money import Money
from travelos.inquiry.models import Inquiry
from travelos.packages.records import ItineraryRecord
from travelos.pricing.records import PriceCalculationRecord
from travelos.pricing.store import PricingStore
from travelos.quotes.errors import (
    QuoteNotFoundError,
    QuotePricingError,
    QuoteStateError,
    QuoteValidationError,
)
from travelos.quotes.models import (
    LineKind,
    QuoteLine,
    QuoteRevision,
    QuoteState,
    RevisionDiff,
    diff_revisions,
)
from travelos.quotes.overrides import QuotePriceOverrideService
from travelos.quotes.records import QuoteLineRecord, QuoteRecord, QuoteRevisionRecord


@dataclass(frozen=True, slots=True)
class QuoteView:
    """A quote and every revision, rebuilt from the append-only rows."""

    quote: QuoteRecord
    revisions: tuple[QuoteRevision, ...]

    @property
    def current(self) -> QuoteRevision:
        return self.revisions[-1]


class QuoteStore:
    """Writes and reads quotes for one tenant."""

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
    # Creation
    # ------------------------------------------------------------------

    def create(
        self,
        *,
        calculation_id: UUID,
        itinerary_id: UUID,
        inquiry_id: UUID | None = None,
        override_id: UUID | None = None,
        actor: Actor | None = None,
    ) -> QuoteRecord:
        """Create a draft quote with revision 1; the caller owns the transaction."""

        if inquiry_id is not None:
            self._require_inquiry(inquiry_id)

        calculation = self._require_calculation(calculation_id, override_id=override_id)
        itinerary = self._require_itinerary(itinerary_id)
        self._check_inquiry(itinerary, inquiry_id)

        expires_at = self._expiry(calculation)
        revision = self._revision(calculation, itinerary_id, number=1, override_id=override_id)
        quote = QuoteRecord(
            tenant_id=self._tenant_id,
            inquiry_id=inquiry_id,
            current_revision=1,
            state=QuoteState.DRAFT.value,
            expires_at=expires_at,
        )
        self._db.add(quote)
        self._db.flush()

        self._insert_revision(quote, revision, calculation, override_id=override_id)
        self._audit.record(
            self._db,
            action="quote.created",
            actor=actor or Actor.system(),
            subject_type="quote",
            subject_id=quote.id,
            after={
                "inquiry_id": str(inquiry_id) if inquiry_id is not None else None,
                "state": quote.state,
                "revision_number": revision.number,
                "itinerary_id": str(revision.itinerary_id),
                "price_calculation_id": str(revision.price_calculation_id),
                "pricing_override_id": (str(override_id) if override_id is not None else None),
                "total_paise": revision.total.paise,
                "currency": revision.total.currency,
                "line_count": len(revision.lines),
                "expires_at": expires_at.isoformat(),
            },
        )
        return quote

    def add_revision(
        self,
        quote_id: UUID,
        *,
        calculation_id: UUID,
        itinerary_id: UUID,
        override_id: UUID | None = None,
        actor: Actor | None = None,
    ) -> QuoteRecord:
        """Append a revision to a draft quote and record its exact diff."""

        quote = self._require_quote(quote_id)
        if quote.state != QuoteState.DRAFT.value:
            raise QuoteStateError(
                f"quote {quote_id} is {quote.state}; a new revision requires DRAFT"
            )

        calculation = self._require_calculation(calculation_id, override_id=override_id)
        itinerary = self._require_itinerary(itinerary_id)
        self._check_inquiry(itinerary, quote.inquiry_id)

        previous = self._pure_revision(self._revision_row(quote.id, quote.current_revision))
        number = quote.current_revision + 1
        revision = self._revision(calculation, itinerary_id, number=number, override_id=override_id)
        diff = diff_revisions(previous, revision)
        expires_at = self._expiry(calculation)

        self._insert_revision(quote, revision, calculation, diff=diff, override_id=override_id)
        quote.current_revision = number
        quote.expires_at = expires_at
        self._db.flush()

        self._audit.record(
            self._db,
            action="quote.revision.created",
            actor=actor or Actor.system(),
            subject_type="quote",
            subject_id=quote.id,
            after={
                "revision_number": number,
                "previous_number": previous.number,
                "itinerary_id": str(revision.itinerary_id),
                "price_calculation_id": str(revision.price_calculation_id),
                "pricing_override_id": (str(override_id) if override_id is not None else None),
                "total_paise": revision.total.paise,
                "total_delta_paise": diff.total_delta.paise,
                "itinerary_changed": diff.itinerary_changed,
                "added_lines": sum(1 for item in diff.lines if item.change.value == "added"),
                "removed_lines": sum(1 for item in diff.lines if item.change.value == "removed"),
                "changed_lines": sum(1 for item in diff.lines if item.change.value == "changed"),
                "expires_at": expires_at.isoformat(),
            },
        )
        return quote

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def load(self, quote_id: UUID) -> QuoteView:
        """Rebuild the quote and every immutable revision, verifying the totals."""

        quote = self._require_quote(quote_id)
        rows = list(
            self._db.execute(
                select(QuoteRevisionRecord)
                .where(
                    QuoteRevisionRecord.tenant_id == self._tenant_id,
                    QuoteRevisionRecord.quote_id == quote.id,
                )
                .order_by(QuoteRevisionRecord.number)
            ).scalars()
        )
        if not rows:
            raise QuoteValidationError(f"quote {quote_id} has no revisions")
        if quote.current_revision != rows[-1].number:
            raise QuoteValidationError(
                f"quote {quote_id} points at revision {quote.current_revision}, "
                f"but its newest stored revision is {rows[-1].number}"
            )
        return QuoteView(
            quote=quote,
            revisions=tuple(self._pure_revision(row) for row in rows),
        )

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _require_quote(self, quote_id: UUID) -> QuoteRecord:
        quote = self._db.get(QuoteRecord, quote_id)
        if quote is None or quote.tenant_id != self._tenant_id:
            raise QuoteNotFoundError(f"quote {quote_id} was not found")
        return quote

    def _require_inquiry(self, inquiry_id: UUID) -> Inquiry:
        inquiry = self._db.get(Inquiry, inquiry_id)
        if inquiry is None or inquiry.tenant_id != self._tenant_id:
            raise QuoteValidationError(f"inquiry {inquiry_id} was not found")
        return inquiry

    def _require_itinerary(self, itinerary_id: UUID) -> ItineraryRecord:
        itinerary = self._db.get(ItineraryRecord, itinerary_id)
        if itinerary is None or itinerary.tenant_id != self._tenant_id:
            raise QuoteValidationError(f"itinerary {itinerary_id} is not recorded")
        if itinerary.currency != "INR":
            raise QuoteValidationError(
                f"itinerary {itinerary_id} is in {itinerary.currency}; quotes are INR-only"
            )
        return itinerary

    def _check_inquiry(self, itinerary: ItineraryRecord, inquiry_id: UUID | None) -> None:
        if (
            inquiry_id is not None
            and itinerary.inquiry_id is not None
            and itinerary.inquiry_id != inquiry_id
        ):
            raise QuoteValidationError(
                f"itinerary {itinerary.id} belongs to inquiry {itinerary.inquiry_id}, "
                f"not {inquiry_id}"
            )

    def _require_calculation(
        self, calculation_id: UUID, *, override_id: UUID | None
    ) -> PriceCalculationRecord:
        calculation = PricingStore(self._db, tenant_id=self._tenant_id).get_calculation(
            calculation_id
        )
        if calculation is None:
            raise QuotePricingError(f"price calculation {calculation_id} was not found")
        if calculation.status != "ok" and override_id is None:
            raise QuotePricingError(
                f"price calculation {calculation_id} is {calculation.status}; "
                "an owner price override is required before it can become a quote"
            )
        if calculation.currency != "INR":
            raise QuotePricingError(
                f"price calculation {calculation_id} is in {calculation.currency}; "
                "quotes are INR-only"
            )
        if calculation.customer_price_paise is None:
            raise QuotePricingError(f"price calculation {calculation_id} has no customer price")
        if override_id is not None:
            self._check_override(override_id, calculation)
        return calculation

    def _check_override(self, override_id: UUID, calculation: PriceCalculationRecord) -> None:
        override = QuotePriceOverrideService(self._db, tenant_id=self._tenant_id).get(override_id)
        if override.authorised_calculation_id != calculation.id:
            raise QuoteValidationError(
                f"owner price override {override_id} authorised calculation "
                f"{override.authorised_calculation_id}, not {calculation.id}"
            )
        if override.owner_total_paise != calculation.customer_price_paise:
            raise QuotePricingError(
                f"owner price override {override_id} records {override.owner_total_paise} "
                f"paise, but calculation {calculation.id} prices "
                f"{calculation.customer_price_paise} paise"
            )
        if override.currency != calculation.currency:
            raise QuotePricingError(
                f"owner price override {override_id} is in {override.currency}, but "
                f"calculation {calculation.id} is in {calculation.currency}"
            )
        self._refuse_reused_override(override_id)

    def _refuse_reused_override(self, override_id: UUID) -> None:
        existing = (
            self._db.execute(
                select(QuoteRevisionRecord).where(
                    QuoteRevisionRecord.tenant_id == self._tenant_id,
                    QuoteRevisionRecord.pricing_override_id == override_id,
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            raise QuoteValidationError(
                f"owner price override {override_id} already backs revision "
                f"{existing.number} of quote {existing.quote_id}"
            )

    def _expiry(self, calculation: PriceCalculationRecord) -> datetime:
        raw = calculation.outputs.get("expires_at")
        if isinstance(raw, datetime):
            value = raw
        elif isinstance(raw, str):
            try:
                value = datetime.fromisoformat(raw)
            except ValueError as exc:
                raise QuotePricingError(
                    f"price calculation {calculation.id} has an unreadable expiry"
                ) from exc
        else:
            raise QuotePricingError(f"price calculation {calculation.id} has no expiry")
        if value.tzinfo is None:
            raise QuotePricingError(f"price calculation {calculation.id} has a non-timezone expiry")
        return value.astimezone(UTC)

    def _revision(
        self,
        calculation: PriceCalculationRecord,
        itinerary_id: UUID,
        *,
        number: int,
        override_id: UUID | None = None,
    ) -> QuoteRevision:
        self._refuse_reused_calculation(calculation)
        lines = self._lines(calculation)
        if calculation.customer_price_paise is None:
            raise QuotePricingError(f"price calculation {calculation.id} has no customer price")
        total = Money(calculation.customer_price_paise, calculation.currency)
        try:
            return QuoteRevision(
                number=number,
                itinerary_id=itinerary_id,
                price_calculation_id=calculation.id,
                total=total,
                lines=lines,
            )
        except PydanticValidationError as exc:
            raise QuoteValidationError(
                f"price calculation {calculation.id} cannot become a quote revision: {exc}"
            ) from exc

    def _refuse_reused_calculation(self, calculation: PriceCalculationRecord) -> None:
        existing = (
            self._db.execute(
                select(QuoteRevisionRecord).where(
                    QuoteRevisionRecord.tenant_id == self._tenant_id,
                    QuoteRevisionRecord.price_calculation_id == calculation.id,
                )
            )
            .scalars()
            .first()
        )
        if existing is not None:
            raise QuoteValidationError(
                f"price calculation {calculation.id} already backs revision "
                f"{existing.number} of quote {existing.quote_id}"
            )
        if calculation.quote_revision_id is not None:
            linked = self._db.get(QuoteRevisionRecord, calculation.quote_revision_id)
            if linked is not None:
                raise QuoteValidationError(
                    f"price calculation {calculation.id} is already linked to revision {linked.id}"
                )

    def _lines(self, calculation: PriceCalculationRecord) -> tuple[QuoteLine, ...]:
        raw_lines = calculation.outputs.get("lines")
        if not isinstance(raw_lines, list) or not raw_lines:
            raise QuoteValidationError(f"price calculation {calculation.id} has no price lines")

        lines: list[QuoteLine] = []
        for position, raw in enumerate(raw_lines):
            if not isinstance(raw, dict):
                raise QuoteValidationError(
                    f"price calculation {calculation.id} has an unusable price line"
                )
            kind = raw.get("kind")
            label = raw.get("label")
            amount = raw.get("amount")
            if (
                not isinstance(kind, str)
                or not isinstance(label, str)
                or not isinstance(amount, dict)
            ):
                raise QuoteValidationError(
                    f"price calculation {calculation.id} has an unusable price line"
                )
            try:
                lines.append(
                    QuoteLine(
                        position=position,
                        kind=LineKind(kind),
                        description=label,
                        amount=Money.from_dict(amount),
                    )
                )
            except (DomainValidationError, PydanticValidationError, ValueError) as exc:
                raise QuoteValidationError(
                    f"price calculation {calculation.id} has an unusable price line: {exc}"
                ) from exc
        return tuple(lines)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _insert_revision(
        self,
        quote: QuoteRecord,
        revision: QuoteRevision,
        calculation: PriceCalculationRecord,
        *,
        diff: RevisionDiff | None = None,
        override_id: UUID | None = None,
    ) -> QuoteRevisionRecord:
        revision_id = calculation.quote_revision_id or uuid7()
        row = QuoteRevisionRecord(
            id=revision_id,
            tenant_id=self._tenant_id,
            quote_id=quote.id,
            number=revision.number,
            itinerary_id=revision.itinerary_id,
            price_calculation_id=revision.price_calculation_id,
            total_paise=revision.total.paise,
            currency=revision.total.currency,
            diff_from_previous=None if diff is None else diff.model_dump(mode="json"),
            pricing_override_id=override_id,
        )
        self._db.add(row)
        self._db.flush()

        for line in revision.lines:
            self._db.add(
                QuoteLineRecord(
                    tenant_id=self._tenant_id,
                    revision_id=row.id,
                    position=line.position,
                    kind=line.kind.value,
                    description=line.description,
                    snapshot_id=line.snapshot_id,
                    amount_paise=line.amount.paise,
                    currency=line.amount.currency,
                )
            )
        self._db.flush()
        return row

    def _revision_row(self, quote_id: UUID, number: int) -> QuoteRevisionRecord:
        row = (
            self._db.execute(
                select(QuoteRevisionRecord).where(
                    QuoteRevisionRecord.tenant_id == self._tenant_id,
                    QuoteRevisionRecord.quote_id == quote_id,
                    QuoteRevisionRecord.number == number,
                )
            )
            .scalars()
            .first()
        )
        if row is None:
            raise QuoteValidationError(f"quote {quote_id} has no stored revision {number}")
        return row

    def _pure_revision(self, row: QuoteRevisionRecord) -> QuoteRevision:
        line_rows = list(
            self._db.execute(
                select(QuoteLineRecord)
                .where(
                    QuoteLineRecord.tenant_id == self._tenant_id,
                    QuoteLineRecord.revision_id == row.id,
                )
                .order_by(QuoteLineRecord.position)
            ).scalars()
        )
        if not line_rows:
            raise QuoteValidationError(f"quote revision {row.id} has no lines")

        try:
            return QuoteRevision(
                number=row.number,
                itinerary_id=row.itinerary_id,
                price_calculation_id=row.price_calculation_id,
                total=Money(row.total_paise, row.currency),
                lines=tuple(
                    QuoteLine(
                        position=line.position,
                        kind=LineKind(line.kind),
                        description=line.description,
                        amount=Money(line.amount_paise, row.currency),
                        snapshot_id=line.snapshot_id,
                    )
                    for line in line_rows
                ),
            )
        except (DomainValidationError, PydanticValidationError, ValueError) as exc:
            raise QuoteValidationError(
                f"stored quote revision {row.id} is inconsistent: {exc}"
            ) from exc


__all__ = ["QuoteStore", "QuoteView"]
