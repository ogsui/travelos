"""Pure quote revision, diff and acceptance-window models (task 016a).

A quote revision is the immutable customer-facing snapshot of one stored price
calculation. It copies the calculator's line breakdown instead of allocating a
customer price per supplier item: the calculator returns aggregate, exact lines,
and splitting them per item would invent an allocation rule. Item-level detail
(fare rules, snapshot references) is read from the cited itinerary.

All amounts are integer paise. Nothing here calls a clock, a database, a network
or an LLM.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

from travelos.core.money import Money

_INR = "INR"


class QuoteState(StrEnum):
    """The documented quote workflow, mirrored for validation and display."""

    DRAFT = "DRAFT"
    PENDING_APPROVAL = "PENDING_APPROVAL"
    APPROVED = "APPROVED"
    SENT = "SENT"
    VIEWED = "VIEWED"
    ACCEPTED = "ACCEPTED"
    EXPIRED = "EXPIRED"
    REVISED = "REVISED"
    REJECTED = "REJECTED"


class LineKind(StrEnum):
    """The stable kinds of a price line, mirroring ``pricing.PriceLine``."""

    SUPPLIER = "supplier"
    MARGIN = "margin"
    TAX = "tax"
    GATEWAY = "gateway"
    ROUNDING = "rounding"
    PROPOSAL = "proposal"


class AcceptanceStatus(StrEnum):
    """Why a quote may or may not be accepted right now."""

    ELIGIBLE = "eligible"
    NOT_SENT = "not_sent"
    EXPIRED = "expired"
    ALREADY_ACCEPTED = "already_accepted"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )


class QuoteLine(_FrozenModel):
    """One exact line of the customer price."""

    position: Annotated[int, Field(ge=0)]
    kind: LineKind
    description: Annotated[str, Field(min_length=1, max_length=200)]
    amount: Money
    snapshot_id: UUID | None = None

    @model_validator(mode="after")
    def _inr_only(self) -> Self:
        if self.amount.currency != _INR:
            raise ValueError("quote lines must be in INR")
        return self

    @property
    def key(self) -> tuple[LineKind, str]:
        """Identity used by the revision diff."""

        return (self.kind, self.description)


class QuoteRevision(_FrozenModel):
    """One immutable revision whose lines add up to its customer total."""

    number: Annotated[int, Field(ge=1)]
    itinerary_id: UUID
    price_calculation_id: UUID
    total: Money
    lines: Annotated[tuple[QuoteLine, ...], Field(min_length=1)]

    @model_validator(mode="after")
    def _exact_total_and_unique_lines(self) -> Self:
        if self.total.currency != _INR:
            raise ValueError("a quote revision must be in INR")
        if self.total.paise < 0:
            raise ValueError("a quote revision total must not be negative")

        positions = [line.position for line in self.lines]
        if len(positions) != len(set(positions)):
            raise ValueError("quote line positions must be unique")
        keys = [line.key for line in self.lines]
        if len(keys) != len(set(keys)):
            raise ValueError("quote line descriptions must be unique within their kind")

        added = Money.zero()
        for line in self.lines:
            added = added + line.amount
        if added != self.total:
            raise ValueError(
                f"quote lines add up to {added.paise} paise, not {self.total.paise} paise"
            )
        return self


class LineChange(StrEnum):
    ADDED = "added"
    REMOVED = "removed"
    CHANGED = "changed"


class LineDelta(_FrozenModel):
    """One line's change between two revisions, in exact paise."""

    change: LineChange
    kind: LineKind
    description: Annotated[str, Field(min_length=1, max_length=200)]
    before: Money | None = None
    after: Money | None = None
    delta: Money

    @model_validator(mode="after")
    def _matches_the_change(self) -> Self:
        amounts = (self.before, self.after, self.delta)
        if any(amount is not None and amount.currency != _INR for amount in amounts):
            raise ValueError("diff amounts must be in INR")

        if self.change is LineChange.ADDED:
            if self.before is not None or self.after is None or self.delta != self.after:
                raise ValueError("an added line has only an after amount")
        elif self.change is LineChange.REMOVED:
            if self.before is None or self.after is not None or self.delta != -self.before:
                raise ValueError("a removed line has only a before amount")
        elif self.before is None or self.after is None or self.delta != self.after - self.before:
            raise ValueError("a changed line needs both amounts and their exact delta")
        return self


class RevisionDiff(_FrozenModel):
    """What changed between the previous revision and this one."""

    previous_number: Annotated[int, Field(ge=1)]
    current_number: Annotated[int, Field(ge=1)]
    total_before: Money
    total_after: Money
    total_delta: Money
    itinerary_changed: bool
    lines: tuple[LineDelta, ...] = ()

    @model_validator(mode="after")
    def _exact_and_consistent(self) -> Self:
        if self.current_number <= self.previous_number:
            raise ValueError("a revision diff must move forward")
        amounts = (self.total_before, self.total_after, self.total_delta)
        if any(amount.currency != _INR for amount in amounts):
            raise ValueError("diff totals must be in INR")
        if self.total_after - self.total_before != self.total_delta:
            raise ValueError("total_delta does not equal total_after minus total_before")
        line_delta = sum(item.delta.paise for item in self.lines)
        if line_delta != self.total_delta.paise:
            raise ValueError("line deltas do not add up to total_delta")
        return self

    @property
    def changed(self) -> bool:
        return bool(self.lines) or self.itinerary_changed


def diff_revisions(previous: QuoteRevision, current: QuoteRevision) -> RevisionDiff:
    """Explain an exact line-by-line change; unchanged lines are omitted."""

    before = {line.key: line for line in previous.lines}
    after = {line.key: line for line in current.lines}
    changes: list[LineDelta] = []

    for key, old in before.items():
        new = after.get(key)
        if new is None:
            changes.append(
                LineDelta(
                    change=LineChange.REMOVED,
                    kind=old.kind,
                    description=old.description,
                    before=old.amount,
                    after=None,
                    delta=-old.amount,
                )
            )
        elif new.amount != old.amount:
            changes.append(
                LineDelta(
                    change=LineChange.CHANGED,
                    kind=old.kind,
                    description=old.description,
                    before=old.amount,
                    after=new.amount,
                    delta=new.amount - old.amount,
                )
            )

    for key, new in after.items():
        if key not in before:
            changes.append(
                LineDelta(
                    change=LineChange.ADDED,
                    kind=new.kind,
                    description=new.description,
                    before=None,
                    after=new.amount,
                    delta=new.amount,
                )
            )

    return RevisionDiff(
        previous_number=previous.number,
        current_number=current.number,
        total_before=previous.total,
        total_after=current.total,
        total_delta=current.total - previous.total,
        itinerary_changed=previous.itinerary_id != current.itinerary_id,
        lines=tuple(changes),
    )


def acceptance_status(
    *,
    state: QuoteState,
    expires_at: datetime,
    now: datetime,
) -> AcceptanceStatus:
    """Whether a quote can be accepted, without reading a clock.

    An accepted quote stays accepted even after its window closes. Everything
    else expires at ``expires_at`` (inclusive), and only a sent or viewed quote
    is otherwise eligible.
    """

    if expires_at.tzinfo is None or now.tzinfo is None:
        raise ValueError("quote acceptance needs timezone-aware instants")
    if state is QuoteState.ACCEPTED:
        return AcceptanceStatus.ALREADY_ACCEPTED
    if state is QuoteState.EXPIRED or now >= expires_at:
        return AcceptanceStatus.EXPIRED
    if state in (QuoteState.SENT, QuoteState.VIEWED):
        return AcceptanceStatus.ELIGIBLE
    return AcceptanceStatus.NOT_SENT


__all__ = [
    "AcceptanceStatus",
    "LineChange",
    "LineDelta",
    "LineKind",
    "QuoteLine",
    "QuoteRevision",
    "QuoteState",
    "RevisionDiff",
    "acceptance_status",
    "diff_revisions",
]
