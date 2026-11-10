"""Create and read multi-option quote groups (task 016b1).

A group is presentation metadata, not a new price: every member quote keeps its
own stored calculation and revisions. Staff supply the title, labels and order;
nothing here ranks tiers, derives a label from a price or merges quotes.

A group is immutable once created. Changing its title, labels or membership means
creating a new group, so a rendered multi-option document always describes the
exact rows it was built from. A quote may belong to only one group.

Every write is tenant-scoped and audited; the caller owns the transaction.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.core.audit import Actor, AuditWriter
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.inquiry.models import Inquiry
from travelos.quotes.errors import (
    QuoteGroupNotFoundError,
    QuoteGroupValidationError,
)
from travelos.quotes.models import QuoteGroupDraft
from travelos.quotes.records import (
    QuoteGroupOptionRecord,
    QuoteGroupRecord,
    QuoteRecord,
)

_TITLE_MAX = 200
_NOTE_MAX = 2_000


@dataclass(frozen=True, slots=True)
class QuoteGroupView:
    """A group, its ordered options and the quote rows they name."""

    group: QuoteGroupRecord
    options: tuple[QuoteGroupOptionRecord, ...]
    quotes: tuple[QuoteRecord, ...]

    @property
    def labels(self) -> tuple[str, ...]:
        return tuple(option.label for option in self.options)


class QuoteGroupStore:
    """Writes and reads quote groups for one tenant."""

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

    def create(
        self,
        *,
        inquiry_id: UUID,
        title: str,
        draft: QuoteGroupDraft,
        note: str | None = None,
        actor: Actor | None = None,
    ) -> QuoteGroupRecord:
        """Store one immutable group; the caller owns the transaction."""

        clean_title = title.strip()
        if not clean_title:
            raise QuoteGroupValidationError("a quote group needs a title")
        if len(clean_title) > _TITLE_MAX:
            raise QuoteGroupValidationError(
                f"a quote group title may be at most {_TITLE_MAX} characters"
            )

        clean_note = note.strip() if note is not None else None
        if clean_note is not None and len(clean_note) > _NOTE_MAX:
            raise QuoteGroupValidationError(
                f"a quote group note may be at most {_NOTE_MAX} characters"
            )

        inquiry = self._require_inquiry(inquiry_id)
        for option in draft.options:
            self._require_quote(option.quote_id, inquiry_id)

        group = QuoteGroupRecord(
            tenant_id=self._tenant_id,
            inquiry_id=inquiry.id,
            title=clean_title,
            note=clean_note,
        )
        self._db.add(group)
        self._db.flush()

        for position, option in enumerate(draft.options):
            self._db.add(
                QuoteGroupOptionRecord(
                    tenant_id=self._tenant_id,
                    group_id=group.id,
                    quote_id=option.quote_id,
                    label=option.label,
                    position=position,
                )
            )
        self._db.flush()

        self._audit.record(
            self._db,
            action="quote.group.created",
            actor=actor or Actor.system(),
            subject_type="quote_group",
            subject_id=group.id,
            after={
                "inquiry_id": str(inquiry.id),
                "title": clean_title,
                "option_count": len(draft.options),
                "labels": [option.label for option in draft.options],
                "quote_ids": [str(option.quote_id) for option in draft.options],
            },
        )
        return group

    def load(self, group_id: UUID) -> QuoteGroupView:
        """Load a group and its options, refusing a stale cross-inquiry member."""

        group = self._db.get(QuoteGroupRecord, group_id)
        if group is None or group.tenant_id != self._tenant_id:
            raise QuoteGroupNotFoundError(f"quote group {group_id} was not found")

        options = tuple(
            self._db.execute(
                select(QuoteGroupOptionRecord)
                .where(
                    QuoteGroupOptionRecord.tenant_id == self._tenant_id,
                    QuoteGroupOptionRecord.group_id == group.id,
                )
                .order_by(QuoteGroupOptionRecord.position)
            ).scalars()
        )
        if not options:
            raise QuoteGroupValidationError(f"quote group {group_id} has no options")

        quotes: list[QuoteRecord] = []
        for option in options:
            quote = self._require_quote(option.quote_id, group.inquiry_id, grouped=False)
            quotes.append(quote)
        return QuoteGroupView(group=group, options=options, quotes=tuple(quotes))

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _require_inquiry(self, inquiry_id: UUID) -> Inquiry:
        inquiry = self._db.get(Inquiry, inquiry_id)
        if inquiry is None or inquiry.tenant_id != self._tenant_id:
            raise QuoteGroupValidationError(f"inquiry {inquiry_id} was not found")
        return inquiry

    def _require_quote(
        self,
        quote_id: UUID,
        inquiry_id: UUID,
        *,
        grouped: bool = True,
    ) -> QuoteRecord:
        quote = self._db.get(QuoteRecord, quote_id)
        if quote is None or quote.tenant_id != self._tenant_id:
            raise QuoteGroupValidationError(f"quote {quote_id} was not found")
        if quote.inquiry_id is None:
            raise QuoteGroupValidationError(
                f"quote {quote_id} has no inquiry and cannot join a group"
            )
        if quote.inquiry_id != inquiry_id:
            raise QuoteGroupValidationError(
                f"quote {quote_id} belongs to inquiry {quote.inquiry_id}, not {inquiry_id}"
            )
        if grouped:
            existing = (
                self._db.execute(
                    select(QuoteGroupOptionRecord).where(
                        QuoteGroupOptionRecord.tenant_id == self._tenant_id,
                        QuoteGroupOptionRecord.quote_id == quote.id,
                    )
                )
                .scalars()
                .first()
            )
            if existing is not None:
                raise QuoteGroupValidationError(
                    f"quote {quote_id} already belongs to group {existing.group_id}"
                )
        return quote


__all__ = ["QuoteGroupStore", "QuoteGroupView"]
