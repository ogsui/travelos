"""Persist quote revisions that match a stored price calculation exactly (016a).

A quote is one option under an inquiry. Each revision is immutable and copies
the calculator's breakdown lines, so the customer total can always be checked
against the calculation that produced it. Adding a revision stores an exact
integer-paise diff; the quote expiry is the calculation's expiry, never an
invented validity.

Every margin, tax rate and competitor value here is a synthetic engineering
fixture, not owner or accountant policy.
"""

from __future__ import annotations

from datetime import timedelta
from uuid import uuid4

import pytest
from quote_fixtures import (
    NOW,
    make_group,
    make_inquiry,
    make_itinerary,
    make_quote_for,
    record_price,
)
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.errors import AppendOnlyError
from travelos.core.models import AuditLog
from travelos.core.money import money
from travelos.pricing.records import PriceCalculationRecord
from travelos.quotes.errors import (
    QuoteGroupNotFoundError,
    QuoteGroupValidationError,
    QuoteNotFoundError,
    QuotePricingError,
    QuoteStateError,
    QuoteValidationError,
)
from travelos.quotes.groups import QuoteGroupStore
from travelos.quotes.models import (
    AcceptanceStatus,
    QuoteGroupDraft,
    QuoteGroupOption,
    QuoteState,
    acceptance_status,
)
from travelos.quotes.records import (
    QuoteGroupOptionRecord,
    QuoteGroupRecord,
    QuoteLineRecord,
    QuoteRecord,
    QuoteRevisionRecord,
)
from travelos.quotes.store import QuoteStore

pytestmark = pytest.mark.integration

# ---------------------------------------------------------------------------
# Creation
# ---------------------------------------------------------------------------


def test_a_quote_revision_copies_the_calculations_total_and_lines_exactly(
    db: Session,
) -> None:
    itinerary_id = make_itinerary(db)
    calculation, result = record_price(db)
    assert result.customer_price is not None

    quote = QuoteStore(db).create(
        calculation_id=calculation.id,
        itinerary_id=itinerary_id,
    )

    assert quote.state == QuoteState.DRAFT.value
    assert quote.current_revision == 1
    assert quote.expires_at == result.expires_at
    assert quote.accept_token_hash is None

    view = QuoteStore(db).load(quote.id)
    assert len(view.revisions) == 1
    current = view.current
    assert current.number == 1
    assert current.total == result.customer_price
    assert current.total.paise == calculation.customer_price_paise
    assert sum(line.amount.paise for line in current.lines) == current.total.paise
    assert len(current.lines) == len(result.lines)
    assert [line.kind.value for line in current.lines] == [line.kind for line in result.lines]

    revision_id = db.execute(
        select(QuoteRevisionRecord.id).where(QuoteRevisionRecord.quote_id == quote.id)
    ).scalar_one()
    rows = list(
        db.execute(
            select(QuoteLineRecord).where(QuoteLineRecord.revision_id == revision_id)
        ).scalars()
    )
    assert len(rows) == len(result.lines)
    assert sum(row.amount_paise for row in rows) == result.customer_price.paise


def test_creating_a_quote_records_an_audit_entry_without_customer_data(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    calculation, result = record_price(db)
    assert result.customer_price is not None

    quote = QuoteStore(db).create(
        calculation_id=calculation.id,
        itinerary_id=itinerary_id,
        inquiry_id=None,
    )

    db.flush()
    entries = list(
        db.execute(
            select(AuditLog).where(
                AuditLog.action == "quote.created",
                AuditLog.subject_id == quote.id,
            )
        ).scalars()
    )
    assert len(entries) == 1
    after = entries[0].after
    assert after is not None
    assert after["revision_number"] == 1
    assert after["price_calculation_id"] == str(calculation.id)
    assert after["total_paise"] == result.customer_price.paise
    assert "customer" not in after


def test_a_prelinked_calculation_reuses_the_revision_id_it_was_recorded_with(
    db: Session,
) -> None:
    from travelos.core.ids import uuid7

    itinerary_id = make_itinerary(db)
    prelinked = uuid7()
    calculation, _ = record_price(db, quote_revision_id=prelinked)

    quote = QuoteStore(db).create(
        calculation_id=calculation.id,
        itinerary_id=itinerary_id,
    )

    revision = db.get(QuoteRevisionRecord, prelinked)
    assert revision is not None
    assert revision.quote_id == quote.id
    assert revision.price_calculation_id == calculation.id
    assert calculation.quote_revision_id == prelinked


def test_an_ok_quote_is_acceptable_only_after_it_is_sent(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    calculation, result = record_price(db)
    quote = QuoteStore(db).create(
        calculation_id=calculation.id,
        itinerary_id=itinerary_id,
    )

    assert quote.expires_at == result.expires_at
    assert quote.expires_at is not None
    assert (
        acceptance_status(
            state=QuoteState(quote.state),
            expires_at=quote.expires_at,
            now=NOW,
        )
        is AcceptanceStatus.NOT_SENT
    )

    quote.state = QuoteState.SENT.value
    db.flush()
    assert (
        acceptance_status(
            state=QuoteState(quote.state),
            expires_at=quote.expires_at,
            now=NOW,
        )
        is AcceptanceStatus.ELIGIBLE
    )

    quote.expires_at = NOW
    db.flush()
    assert (
        acceptance_status(
            state=QuoteState(quote.state),
            expires_at=quote.expires_at,
            now=NOW,
        )
        is AcceptanceStatus.EXPIRED
    )


# ---------------------------------------------------------------------------
# Refusals
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("kwargs", "match"),
    [
        ({"proposed": money(100_000)}, "below_floor"),
        ({"competitor": money(105_000)}, "uncompetitive"),
        ({"tcs_enabled": True}, "needs_config"),
    ],
)
def test_a_non_ok_calculation_cannot_become_a_quote(
    db: Session,
    kwargs: dict[str, object],
    match: str,
) -> None:
    itinerary_id = make_itinerary(db)
    calculation, _ = record_price(db, **kwargs)

    with pytest.raises(QuotePricingError, match=match):
        QuoteStore(db).create(
            calculation_id=calculation.id,
            itinerary_id=itinerary_id,
        )

    db.flush()
    assert db.execute(select(QuoteRecord)).scalars().first() is None


def test_an_unknown_calculation_is_refused(db: Session) -> None:
    itinerary_id = make_itinerary(db)

    with pytest.raises(QuotePricingError, match="was not found"):
        QuoteStore(db).create(calculation_id=uuid4(), itinerary_id=itinerary_id)


def test_an_unknown_itinerary_is_refused(db: Session) -> None:
    calculation, _ = record_price(db)

    with pytest.raises(QuoteValidationError, match="is not recorded"):
        QuoteStore(db).create(calculation_id=calculation.id, itinerary_id=uuid4())


def test_a_calculation_without_an_expiry_is_refused(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    calculation, _ = record_price(db)

    broken = PriceCalculationRecord(
        tenant_id=DEFAULT_TENANT_ID,
        quote_revision_id=None,
        rule_set_id=calculation.rule_set_id,
        tax_table_id=calculation.tax_table_id,
        status="ok",
        currency="INR",
        rule_version=calculation.rule_version,
        tax_table_version=calculation.tax_table_version,
        floor_paise=calculation.floor_paise,
        target_paise=calculation.target_paise,
        ceiling_paise=calculation.ceiling_paise,
        recommended_paise=calculation.recommended_paise,
        customer_price_paise=calculation.customer_price_paise,
        inputs={},
        outputs={"status": "ok", "lines": []},
    )
    db.add(broken)
    db.flush()

    with pytest.raises(QuotePricingError, match="no expiry"):
        QuoteStore(db).create(calculation_id=broken.id, itinerary_id=itinerary_id)


def test_a_calculation_cannot_back_two_revisions_or_two_quotes(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    calculation, _ = record_price(db)
    store = QuoteStore(db)
    quote = store.create(calculation_id=calculation.id, itinerary_id=itinerary_id)

    with pytest.raises(QuoteValidationError, match="already backs"):
        store.create(calculation_id=calculation.id, itinerary_id=itinerary_id)

    with pytest.raises(QuoteValidationError, match="already backs"):
        store.add_revision(
            quote.id,
            calculation_id=calculation.id,
            itinerary_id=itinerary_id,
        )


# ---------------------------------------------------------------------------
# Second revision and diff
# ---------------------------------------------------------------------------


def test_a_second_draft_revision_records_the_diff_and_moves_the_expiry(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    first_calculation, first_result = record_price(db)
    store = QuoteStore(db)
    quote = store.create(calculation_id=first_calculation.id, itinerary_id=itinerary_id)

    second_calculation, second_result = record_price(
        db,
        net_paise=110_000,
        now=NOW + timedelta(hours=1),
    )
    store.add_revision(
        quote.id,
        calculation_id=second_calculation.id,
        itinerary_id=itinerary_id,
    )

    assert quote.current_revision == 2
    assert quote.expires_at == second_result.expires_at
    assert first_result.expires_at is not None
    assert second_result.expires_at is not None
    assert quote.expires_at > first_result.expires_at

    view = store.load(quote.id)
    assert [revision.number for revision in view.revisions] == [1, 2]
    assert first_result.customer_price is not None
    assert second_result.customer_price is not None
    assert view.revisions[0].total == first_result.customer_price
    assert view.revisions[1].total == second_result.customer_price

    revision_row = db.execute(
        select(QuoteRevisionRecord).where(
            QuoteRevisionRecord.quote_id == quote.id,
            QuoteRevisionRecord.number == 2,
        )
    ).scalar_one()
    assert revision_row.diff_from_previous is not None
    assert revision_row.diff_from_previous["previous_number"] == 1
    assert revision_row.diff_from_previous["current_number"] == 2
    assert (
        revision_row.diff_from_previous["total_delta"]["paise"]
        == second_result.customer_price.paise - first_result.customer_price.paise
    )

    db.flush()
    entries = list(
        db.execute(
            select(AuditLog).where(
                AuditLog.action == "quote.revision.created",
                AuditLog.subject_id == quote.id,
            )
        ).scalars()
    )
    assert len(entries) == 1
    after = entries[0].after
    assert after is not None
    assert after["revision_number"] == 2


def test_a_revision_is_refused_once_the_quote_is_no_longer_draft(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    calculation, _ = record_price(db)
    store = QuoteStore(db)
    quote = store.create(calculation_id=calculation.id, itinerary_id=itinerary_id)

    quote.state = QuoteState.SENT.value
    db.flush()
    second_calculation, _ = record_price(db, net_paise=110_000)

    with pytest.raises(QuoteStateError, match="DRAFT"):
        store.add_revision(
            quote.id,
            calculation_id=second_calculation.id,
            itinerary_id=itinerary_id,
        )


def test_an_unknown_quote_cannot_gain_a_revision(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    calculation, _ = record_price(db)

    with pytest.raises(QuoteNotFoundError):
        QuoteStore(db).add_revision(
            uuid4(),
            calculation_id=calculation.id,
            itinerary_id=itinerary_id,
        )


# ---------------------------------------------------------------------------
# Tenant isolation and immutability
# ---------------------------------------------------------------------------


def test_a_quote_is_invisible_to_another_tenant(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    calculation, _ = record_price(db)
    quote = QuoteStore(db).create(
        calculation_id=calculation.id,
        itinerary_id=itinerary_id,
    )

    other = QuoteStore(db, tenant_id=uuid4())

    with pytest.raises(QuoteNotFoundError):
        other.load(quote.id)

    with pytest.raises(QuotePricingError, match="was not found"):
        other.create(calculation_id=calculation.id, itinerary_id=itinerary_id)


def test_a_revision_and_its_lines_are_append_only(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    calculation, _ = record_price(db)
    quote = QuoteStore(db).create(
        calculation_id=calculation.id,
        itinerary_id=itinerary_id,
    )
    revision = db.execute(
        select(QuoteRevisionRecord).where(QuoteRevisionRecord.quote_id == quote.id)
    ).scalar_one()

    revision.number = 99
    with pytest.raises(AppendOnlyError):
        db.flush()
    db.rollback()


def test_a_quote_line_cannot_be_deleted(db: Session) -> None:
    itinerary_id = make_itinerary(db)
    calculation, _ = record_price(db)
    quote = QuoteStore(db).create(
        calculation_id=calculation.id,
        itinerary_id=itinerary_id,
    )
    revision = db.execute(
        select(QuoteRevisionRecord).where(QuoteRevisionRecord.quote_id == quote.id)
    ).scalar_one()
    line = (
        db.execute(select(QuoteLineRecord).where(QuoteLineRecord.revision_id == revision.id))
        .scalars()
        .first()
    )
    assert line is not None

    db.delete(line)
    with pytest.raises(AppendOnlyError):
        db.flush()
    db.rollback()


# ---------------------------------------------------------------------------
# Database constraints
# ---------------------------------------------------------------------------


def test_the_database_refuses_an_unknown_quote_state(db: Session) -> None:
    db.add(
        QuoteRecord(
            tenant_id=DEFAULT_TENANT_ID,
            inquiry_id=None,
            current_revision=0,
            state="NOPE",
            expires_at=NOW,
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


def test_the_database_refuses_a_malformed_accept_token_hash(db: Session) -> None:
    db.add(
        QuoteRecord(
            tenant_id=DEFAULT_TENANT_ID,
            inquiry_id=None,
            current_revision=0,
            state=QuoteState.DRAFT.value,
            expires_at=NOW,
            accept_token_hash="not-a-sha256",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()


# ---------------------------------------------------------------------------
# Multi-option groups (task 016b1)
# ---------------------------------------------------------------------------


def test_a_group_loads_its_options_in_the_order_given(db: Session) -> None:
    inquiry_id = make_inquiry(db)
    first = make_quote_for(db, inquiry_id)
    second = make_quote_for(db, inquiry_id, net_paise=110_000)
    group = make_group(db, inquiry_id, [first.id, second.id], labels=["Beach", "City"])

    view = QuoteGroupStore(db).load(group.id)

    assert view.group.inquiry_id == inquiry_id
    assert view.labels == ("Beach", "City")
    assert [option.position for option in view.options] == [0, 1]
    assert [quote.id for quote in view.quotes] == [first.id, second.id]


def test_a_group_refuses_a_quote_from_another_inquiry(db: Session) -> None:
    first_inquiry = make_inquiry(db)
    second_inquiry = make_inquiry(db)
    first = make_quote_for(db, first_inquiry)
    second = make_quote_for(db, second_inquiry)

    with pytest.raises(QuoteGroupValidationError, match="belongs to inquiry"):
        make_group(db, first_inquiry, [first.id, second.id])


def test_a_group_refuses_a_quote_without_an_inquiry(db: Session) -> None:
    inquiry_id = make_inquiry(db)
    grouped = make_quote_for(db, inquiry_id)
    ungrouped = make_quote_for(db, None)

    with pytest.raises(QuoteGroupValidationError, match="has no inquiry"):
        make_group(db, inquiry_id, [grouped.id, ungrouped.id])


def test_a_quote_can_belong_to_only_one_group(db: Session) -> None:
    inquiry_id = make_inquiry(db)
    first = make_quote_for(db, inquiry_id)
    second = make_quote_for(db, inquiry_id, net_paise=110_000)
    third = make_quote_for(db, inquiry_id, net_paise=120_000)
    make_group(db, inquiry_id, [first.id, second.id])

    with pytest.raises(QuoteGroupValidationError, match="already belongs"):
        make_group(db, inquiry_id, [first.id, third.id])


def test_an_unknown_quote_is_refused(db: Session) -> None:
    inquiry_id = make_inquiry(db)
    first = make_quote_for(db, inquiry_id)

    with pytest.raises(QuoteGroupValidationError, match="was not found"):
        make_group(db, inquiry_id, [first.id, uuid4()])


def test_an_unknown_inquiry_is_refused(db: Session) -> None:
    draft = QuoteGroupDraft(
        options=(
            QuoteGroupOption(label="Option A", quote_id=uuid4()),
            QuoteGroupOption(label="Option B", quote_id=uuid4()),
        )
    )

    with pytest.raises(QuoteGroupValidationError, match="was not found"):
        QuoteGroupStore(db).create(
            inquiry_id=uuid4(),
            title="Synthetic trip options",
            draft=draft,
        )


def test_a_group_is_invisible_to_another_tenant(db: Session) -> None:
    inquiry_id = make_inquiry(db)
    first = make_quote_for(db, inquiry_id)
    second = make_quote_for(db, inquiry_id, net_paise=110_000)
    group = make_group(db, inquiry_id, [first.id, second.id])

    other = QuoteGroupStore(db, tenant_id=uuid4())

    with pytest.raises(QuoteGroupNotFoundError):
        other.load(group.id)


def test_group_option_rows_are_append_only(db: Session) -> None:
    inquiry_id = make_inquiry(db)
    first = make_quote_for(db, inquiry_id)
    second = make_quote_for(db, inquiry_id, net_paise=110_000)
    group = make_group(db, inquiry_id, [first.id, second.id])
    option = (
        db.execute(
            select(QuoteGroupOptionRecord).where(QuoteGroupOptionRecord.group_id == group.id)
        )
        .scalars()
        .first()
    )
    assert option is not None

    option.label = "Changed"
    with pytest.raises(AppendOnlyError):
        db.flush()
    db.rollback()


def test_a_group_cannot_be_deleted(db: Session) -> None:
    inquiry_id = make_inquiry(db)
    first = make_quote_for(db, inquiry_id)
    second = make_quote_for(db, inquiry_id, net_paise=110_000)
    group = make_group(db, inquiry_id, [first.id, second.id])
    row = db.get(QuoteGroupRecord, group.id)
    assert row is not None

    db.delete(row)
    with pytest.raises(AppendOnlyError):
        db.flush()
    db.rollback()


def test_creating_a_group_is_audited_without_customer_data(db: Session) -> None:
    inquiry_id = make_inquiry(db)
    first = make_quote_for(db, inquiry_id)
    second = make_quote_for(db, inquiry_id, net_paise=110_000)
    group = make_group(db, inquiry_id, [first.id, second.id], labels=["Beach", "City"])

    db.flush()
    entries = list(
        db.execute(
            select(AuditLog).where(
                AuditLog.action == "quote.group.created",
                AuditLog.subject_id == group.id,
            )
        ).scalars()
    )
    assert len(entries) == 1
    after = entries[0].after
    assert after is not None
    assert after["option_count"] == 2
    assert after["labels"] == ["Beach", "City"]
    assert "customer" not in after


def test_the_database_refuses_a_blank_group_title(db: Session) -> None:
    inquiry_id = make_inquiry(db)
    db.add(
        QuoteGroupRecord(
            tenant_id=DEFAULT_TENANT_ID,
            inquiry_id=inquiry_id,
            title="   ",
        )
    )
    with pytest.raises(IntegrityError):
        db.flush()
    db.rollback()
