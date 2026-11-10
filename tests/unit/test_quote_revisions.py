"""Pure quote revision, diff and acceptance-window rules (task 016a).

A revision is the immutable, customer-facing snapshot of one stored price
calculation. Every amount is integer paise, the lines must add up to the total
exactly, and a later revision explains what changed without re-deriving money.

The acceptance predicate is defined here even though the accept endpoint lands in
016c: an expired or unsent quote must never be acceptable, and that rule is
cheaper to test before a token exists.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

from travelos.core.money import money
from travelos.quotes.models import (
    AcceptanceStatus,
    LineChange,
    LineKind,
    QuoteLine,
    QuoteRevision,
    QuoteState,
    acceptance_status,
    diff_revisions,
)

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
ITINERARY = UUID(int=1)
CALCULATION = UUID(int=2)


def line(
    position: int,
    *,
    kind: LineKind = LineKind.SUPPLIER,
    description: str | None = None,
    paise: int = 100_000,
    currency: str = "INR",
) -> QuoteLine:
    return QuoteLine(
        position=position,
        kind=kind,
        description=description or f"{kind.value}-{position}",
        amount=money(paise, currency),
    )


def revision(
    *,
    number: int = 1,
    total_paise: int | None = None,
    lines: tuple[QuoteLine, ...] | None = None,
    itinerary_id: UUID = ITINERARY,
    calculation_id: UUID = CALCULATION,
) -> QuoteRevision:
    built = lines if lines is not None else (line(0, paise=100_000),)
    total = sum(item.amount.paise for item in built) if total_paise is None else total_paise
    return QuoteRevision(
        number=number,
        itinerary_id=itinerary_id,
        price_calculation_id=calculation_id,
        total=money(total),
        lines=built,
    )


# ---------------------------------------------------------------------------
# Shape and invariants
# ---------------------------------------------------------------------------


def test_a_revision_accepts_lines_that_add_up_to_its_total() -> None:
    built = revision(
        lines=(
            line(0, kind=LineKind.SUPPLIER, description="Hotel", paise=100_000),
            line(1, kind=LineKind.TAX, description="GST", paise=5_000),
        )
    )

    assert built.total == money(105_000)
    assert [item.description for item in built.lines] == ["Hotel", "GST"]


def test_lines_that_do_not_add_up_to_the_total_are_refused() -> None:
    with pytest.raises(ValidationError, match="lines add up to 100000 paise"):
        revision(
            total_paise=99_999,
            lines=(line(0, paise=100_000),),
        )


def test_duplicate_positions_are_refused() -> None:
    with pytest.raises(ValidationError, match="positions must be unique"):
        revision(
            lines=(
                line(0, description="Hotel", paise=50_000),
                line(0, description="Flight", paise=50_000),
            )
        )


def test_duplicate_line_keys_are_refused_at_any_position() -> None:
    with pytest.raises(ValidationError, match="line descriptions must be unique"):
        revision(
            lines=(
                line(0, kind=LineKind.SUPPLIER, description="Hotel", paise=50_000),
                line(1, kind=LineKind.SUPPLIER, description="Hotel", paise=50_000),
            )
        )


def test_a_negative_total_is_refused() -> None:
    with pytest.raises(ValidationError, match="total must not be negative"):
        revision(
            total_paise=-1,
            lines=(line(0, paise=-1),),
        )


def test_a_non_inr_line_is_refused() -> None:
    with pytest.raises(ValidationError, match="INR"):
        revision(lines=(line(0, paise=100_000, currency="USD"),))


# ---------------------------------------------------------------------------
# Revision diff
# ---------------------------------------------------------------------------


def test_the_diff_reports_changed_added_and_removed_lines_with_an_exact_delta() -> None:
    previous = revision(
        lines=(
            line(0, kind=LineKind.SUPPLIER, description="Hotel", paise=100_000),
            line(1, kind=LineKind.TAX, description="GST", paise=5_000),
            line(2, kind=LineKind.MARGIN, description="Margin", paise=10_000),
        )
    )
    current = revision(
        number=2,
        lines=(
            line(0, kind=LineKind.SUPPLIER, description="Hotel", paise=110_000),
            line(1, kind=LineKind.TAX, description="GST", paise=5_500),
            line(2, kind=LineKind.GATEWAY, description="Gateway fee", paise=2_000),
        ),
    )

    diff = diff_revisions(previous, current)

    assert diff.previous_number == 1
    assert diff.current_number == 2
    assert diff.total_before == money(115_000)
    assert diff.total_after == money(117_500)
    assert diff.total_delta == money(2_500)
    assert diff.itinerary_changed is False
    assert diff.changed is True

    by_description = {item.description: item for item in diff.lines}
    assert by_description["Hotel"].change is LineChange.CHANGED
    assert by_description["Hotel"].delta == money(10_000)
    assert by_description["GST"].delta == money(500)
    assert by_description["Gateway fee"].change is LineChange.ADDED
    assert by_description["Gateway fee"].before is None
    assert by_description["Margin"].change is LineChange.REMOVED
    assert by_description["Margin"].after is None
    assert sum(item.delta.paise for item in diff.lines) == diff.total_delta.paise


def test_an_unchanged_revision_produces_no_line_changes() -> None:
    previous = revision()
    current = revision(number=2)

    diff = diff_revisions(previous, current)

    assert diff.lines == ()
    assert diff.total_delta == money(0)
    assert diff.changed is False


def test_a_changed_itinerary_is_flagged_even_when_every_line_matches() -> None:
    previous = revision()
    current = revision(number=2, itinerary_id=UUID(int=99))

    diff = diff_revisions(previous, current)

    assert diff.lines == ()
    assert diff.itinerary_changed is True
    assert diff.changed is True


def test_a_changed_calculation_is_reflected_by_the_totals_not_a_hidden_relabel() -> None:
    previous = revision(total_paise=105_000, lines=(line(0, paise=105_000),))
    current = revision(
        number=2,
        total_paise=108_000,
        calculation_id=UUID(int=3),
        lines=(line(0, paise=108_000),),
    )

    diff = diff_revisions(previous, current)

    assert diff.total_delta == money(3_000)
    assert [item.change for item in diff.lines] == [LineChange.CHANGED]


@given(
    before=st.lists(
        st.tuples(
            st.sampled_from(list(LineKind)),
            st.text(alphabet="abcdefghij", min_size=1, max_size=6),
            st.integers(min_value=0, max_value=10_000_000),
        ),
        min_size=1,
        max_size=4,
    ),
    after=st.lists(
        st.tuples(
            st.sampled_from(list(LineKind)),
            st.text(alphabet="abcdefghij", min_size=1, max_size=6),
            st.integers(min_value=0, max_value=10_000_000),
        ),
        min_size=1,
        max_size=4,
    ),
)
@settings(max_examples=50, deadline=None)
def test_every_line_delta_adds_up_to_the_total_delta(
    before: list[tuple[LineKind, str, int]],
    after: list[tuple[LineKind, str, int]],
) -> None:
    def build(number: int, rows: list[tuple[LineKind, str, int]]) -> QuoteRevision:
        unique: dict[tuple[LineKind, str], int] = {}
        for kind, description, paise in rows:
            unique[(kind, description)] = paise
        lines = tuple(
            QuoteLine(
                position=position,
                kind=kind,
                description=description,
                amount=money(paise),
            )
            for position, ((kind, description), paise) in enumerate(unique.items())
        )
        return revision(number=number, lines=lines)

    diff = diff_revisions(build(1, before), build(2, after))

    assert sum(item.delta.paise for item in diff.lines) == diff.total_delta.paise


# ---------------------------------------------------------------------------
# Acceptance window
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("state", "expires_at", "now", "expected"),
    [
        (QuoteState.DRAFT, NOW + timedelta(hours=1), NOW, AcceptanceStatus.NOT_SENT),
        (
            QuoteState.PENDING_APPROVAL,
            NOW + timedelta(hours=1),
            NOW,
            AcceptanceStatus.NOT_SENT,
        ),
        (QuoteState.APPROVED, NOW + timedelta(hours=1), NOW, AcceptanceStatus.NOT_SENT),
        (QuoteState.SENT, NOW + timedelta(hours=1), NOW, AcceptanceStatus.ELIGIBLE),
        (QuoteState.VIEWED, NOW + timedelta(hours=1), NOW, AcceptanceStatus.ELIGIBLE),
        (QuoteState.SENT, NOW, NOW, AcceptanceStatus.EXPIRED),
        (
            QuoteState.VIEWED,
            NOW - timedelta(seconds=1),
            NOW,
            AcceptanceStatus.EXPIRED,
        ),
        (QuoteState.EXPIRED, NOW + timedelta(hours=1), NOW, AcceptanceStatus.EXPIRED),
        (QuoteState.ACCEPTED, NOW + timedelta(hours=1), NOW, AcceptanceStatus.ALREADY_ACCEPTED),
        (QuoteState.ACCEPTED, NOW - timedelta(hours=1), NOW, AcceptanceStatus.ALREADY_ACCEPTED),
        (QuoteState.REVISED, NOW + timedelta(hours=1), NOW, AcceptanceStatus.NOT_SENT),
        (QuoteState.REJECTED, NOW + timedelta(hours=1), NOW, AcceptanceStatus.NOT_SENT),
    ],
)
def test_only_a_current_unexpired_sent_or_viewed_quote_is_acceptable(
    state: QuoteState,
    expires_at: datetime,
    now: datetime,
    expected: AcceptanceStatus,
) -> None:
    assert acceptance_status(state=state, expires_at=expires_at, now=now) is expected


def test_an_unsent_quote_stays_unacceptable_even_before_its_expiry() -> None:
    assert (
        acceptance_status(
            state=QuoteState.DRAFT,
            expires_at=NOW + timedelta(days=7),
            now=NOW,
        )
        is AcceptanceStatus.NOT_SENT
    )
