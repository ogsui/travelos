"""Pure customer-facing quote HTML rendering (task 016b2a).

The document model is already customer-safe: it holds the stored total and only
``tax`` lines, never supplier cost, margin, gateway, rounding or proposal lines.
The renderer escapes every dynamic value and emits no scripts. Every name,
label, term and amount here is synthetic.
"""

from __future__ import annotations

import base64
from datetime import UTC, date, datetime, timedelta
from uuid import UUID

import pytest
from pydantic import ValidationError

from travelos.core.money import Money, money
from travelos.quotes.models import LineKind, QuoteLine
from travelos.quotes.presentation import Brand, PaymentStage, Terms
from travelos.quotes.render import (
    DayView,
    FareRuleView,
    ItemView,
    OptionView,
    QuoteDocument,
    render_quote_html,
)

PNG_URI = "data:image/png;base64," + base64.b64encode(b"tiny").decode("ascii")
PREPARED = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
EXPIRES = PREPARED + timedelta(days=7)
START = date(2027, 2, 10)
END = date(2027, 2, 13)
FIRST_QUOTE = UUID(int=1)


def brand(**overrides: object) -> Brand:
    data = {
        "display_name": "Synthetic Travel",
        "legal_name": "Synthetic Travel Private Limited",
        "registered_address": "1 Test Road, Panaji, Goa 403001",
        "gstin": "27AAAAA0000A1Z5",
        "logo_data_uri": PNG_URI,
        "primary_color": "#123456",
        "support_email": "help@example.test",
        "support_phone": "+91 90000 00000",
    }
    data.update(overrides)
    return Brand.model_validate(data)


def terms(**overrides: object) -> Terms:
    data = {"version": "synthetic-v1", "text": "Synthetic terms for tests only."}
    data.update(overrides)
    return Terms.model_validate(data)


def schedule() -> tuple[PaymentStage, ...]:
    return (
        PaymentStage(label="Deposit", percent_bps=3000, due="On booking"),
        PaymentStage(label="Balance", percent_bps=7000, due="30 days before travel"),
    )


def tax_line(description: str = "GST 5%", paise: int = 5_000) -> QuoteLine:
    return QuoteLine(
        position=0,
        kind=LineKind.TAX,
        description=description,
        amount=money(paise),
    )


def fare_rule(**overrides: object) -> FareRuleView:
    data = {
        "product": "flight",
        "refundable": True,
        "cancellation_days": 7,
        "change_fee": money(0),
    }
    data.update(overrides)
    return FareRuleView.model_validate(data)


def item(**overrides: object) -> ItemView:
    data = {
        "component_type": "flight",
        "name": "MockAir Delhi to Goa",
        "supplier": "MockAir",
        "city": "Goa",
        "start_date": START,
        "end_date": None,
        "quantity": 2,
        "unit_basis": "traveller",
        "snapshot_ref": str(UUID(int=99)),
        "fare_rule": fare_rule(),
    }
    data.update(overrides)
    return ItemView.model_validate(data)


def option(
    *,
    label: str | None = None,
    quote_id: UUID = FIRST_QUOTE,
    tax_lines: tuple[QuoteLine, ...] | None = None,
    **overrides: object,
) -> OptionView:
    data = {
        "label": label,
        "quote_id": quote_id,
        "revision_number": 1,
        "total": money(123_450),
        "tax_lines": (tax_line(),) if tax_lines is None else tax_lines,
        "start_date": START,
        "end_date": END,
        "travellers": 2,
        "days": (
            DayView(
                day_index=1,
                date=START,
                items=(item(),),
            ),
            DayView(day_index=2, date=START + timedelta(days=1), items=()),
        ),
        "snapshot_refs": (str(UUID(int=99)),),
    }
    data.update(overrides)
    return OptionView.model_validate(data)


def document(**overrides: object) -> QuoteDocument:
    data = {
        "id": UUID(int=1),
        "kind": "quote",
        "title": None,
        "prepared_at": PREPARED,
        "expires_at": EXPIRES,
        "timezone": "Asia/Kolkata",
        "brand": brand(),
        "options": (option(),),
        "payment_schedule": schedule(),
        "terms": terms(),
        "visa_note": "To be confirmed",
    }
    data.update(overrides)
    return QuoteDocument.model_validate(data)


def test_the_document_shows_the_total_and_the_tax_lines() -> None:
    html = render_quote_html(document())

    assert "₹1,234.50" in html
    assert "Total" in html
    assert "GST 5%" in html
    assert "₹50.00" in html


def test_a_non_tax_line_cannot_enter_the_customer_document() -> None:
    margin = QuoteLine(
        position=0,
        kind=LineKind.MARGIN,
        description="Agency margin",
        amount=money(10_000),
    )

    with pytest.raises(ValidationError, match="only tax lines"):
        option(tax_lines=(margin,))


def test_tax_lines_cannot_exceed_the_total() -> None:
    with pytest.raises(ValidationError, match="cannot exceed the total"):
        option(tax_lines=(tax_line(paise=200_000),))


def test_a_negative_tax_line_is_refused() -> None:
    with pytest.raises(ValidationError, match="must not be negative"):
        option(tax_lines=(tax_line(paise=-100),))


def test_every_dynamic_value_is_html_escaped() -> None:
    hostile = "<script>alert('x')</script>"
    html = render_quote_html(
        document(
            kind="group",
            title="Synthetic trip options",
            brand=brand(display_name=hostile),
            terms=terms(text="<img src=x onerror=alert(1)>\nSecond line."),
            options=(option(label="A & B <b>", tax_lines=(tax_line(description="<b>GST</b>"),)),),
        )
    )

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert "<img src=x" not in html
    assert "&lt;img src=x" in html
    assert "A &amp; B &lt;b&gt;" in html
    assert "<b>GST</b>" not in html
    assert "&lt;b&gt;GST&lt;/b&gt;" in html


def test_group_options_render_in_the_staff_order() -> None:
    html = render_quote_html(
        document(
            kind="group",
            title="Synthetic trip options",
            options=(
                option(label="Beach", quote_id=UUID(int=1)),
                option(label="City", quote_id=UUID(int=2)),
            ),
        )
    )

    assert html.index("Beach") < html.index("City")
    assert "Synthetic trip options" in html


def test_an_expired_quote_is_clearly_marked() -> None:
    html = render_quote_html(
        document(
            prepared_at=EXPIRES + timedelta(minutes=1),
            expires_at=EXPIRES,
        )
    )

    assert "expired" in html.lower()


def test_the_payment_schedule_shows_exact_percentages_and_owner_wording() -> None:
    html = render_quote_html(document())

    assert "30%" in html
    assert "70%" in html
    assert "On booking" in html
    assert "30 days before travel" in html


def test_visa_note_is_to_be_confirmed_when_no_source_is_configured() -> None:
    html = render_quote_html(document())

    assert "Visa" in html
    assert "To be confirmed" in html


def test_fare_rules_are_reported_as_supplier_conditions() -> None:
    html = render_quote_html(document())

    assert "Refundable" in html
    assert "7 days" in html
    assert "Change fee" in html
    assert "₹0.00" in html


def test_snapshot_references_are_shown() -> None:
    html = render_quote_html(document())

    assert str(UUID(int=99)) in html
    assert "Price snapshot references" in html


def test_no_snapshot_claim_is_made_without_a_reference() -> None:
    html = render_quote_html(
        document(
            options=(
                option(
                    snapshot_refs=(),
                    days=(DayView(day_index=1, date=START, items=(item(snapshot_ref=None),)),),
                ),
            ),
        )
    )

    assert "supplier snapshot references" not in html


def test_the_terms_version_and_text_are_shown() -> None:
    html = render_quote_html(document())

    assert "synthetic-v1" in html
    assert "Synthetic terms for tests only." in html


def test_naive_instants_are_refused() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        document(prepared_at=datetime(2026, 11, 10, 9, 0))


def test_a_document_needs_at_least_one_option() -> None:
    with pytest.raises(ValidationError, match="at least one option"):
        document(options=())


def test_a_group_needs_a_title() -> None:
    with pytest.raises(ValidationError, match="title"):
        document(kind="group", title=None)


def test_a_single_quote_cannot_carry_a_group_title() -> None:
    with pytest.raises(ValidationError, match="title"):
        document(kind="quote", title="Synthetic trip options")


def test_a_group_option_needs_a_staff_label() -> None:
    with pytest.raises(ValidationError, match="staff label"):
        document(kind="group", title="Synthetic trip options", options=(option(),))


def test_a_single_quote_cannot_label_its_option() -> None:
    with pytest.raises(ValidationError, match="cannot label"):
        document(options=(option(label="Option A"),))


def test_the_payment_schedule_must_add_up_to_100_percent() -> None:
    with pytest.raises(ValidationError, match="100%"):
        document(
            payment_schedule=(PaymentStage(label="Deposit", percent_bps=3000, due="On booking"),)
        )


def test_a_day_outside_the_option_dates_is_refused() -> None:
    with pytest.raises(ValidationError, match="inside the option dates"):
        option(
            days=(DayView(day_index=1, date=START - timedelta(days=1), items=()),),
        )


def test_a_negative_total_is_refused() -> None:
    with pytest.raises(ValidationError, match="negative"):
        option(total=Money(-1, "INR"))


def test_a_usd_tax_line_is_refused() -> None:
    with pytest.raises(ValidationError):
        QuoteLine(
            position=0,
            kind=LineKind.TAX,
            description="GST",
            amount=Money(100, "USD"),
        )
