"""Pure customer-facing quote HTML (task 016b2a).

The document model already holds only customer-safe values: the stored total and
the stored ``tax`` lines. Supplier cost, agency margin, gateway fees, rounding
and proposal lines are not part of this model, so the renderer cannot leak them.

Every dynamic value is HTML-escaped and the output contains no scripts, no
external resources and no local file references. The logo is an embedded data
URI supplied by :mod:`travelos.quotes.presentation`.

This module is pure: it reads no clock, database, file or network. The caller
supplies the prepared instant and the owner configuration.
"""

from __future__ import annotations

import html
from datetime import date, datetime
from typing import Annotated, Literal, Self
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from travelos.core.money import Money
from travelos.quotes.models import LineKind, QuoteLine
from travelos.quotes.presentation import Brand, PaymentStage, Terms

_INR = "INR"
_DASH = "\u2014"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )


class FareRuleView(_FrozenModel):
    """One supplier's reported fare conditions, not an agency cancellation policy."""

    product: Literal["flight", "hotel", "transfer", "activity", "visa"]
    refundable: bool
    cancellation_days: Annotated[int, Field(ge=0, le=365)]
    change_fee: Money

    @model_validator(mode="after")
    def _inr_only(self) -> Self:
        if self.change_fee.currency != _INR:
            raise ValueError("fare-rule amounts must be in INR")
        if self.change_fee.is_negative():
            raise ValueError("a change fee must not be negative")
        return self


class ItemView(_FrozenModel):
    """One scheduled supplier arrangement shown to the customer."""

    component_type: Literal["flight", "hotel", "transfer", "activity", "visa"]
    name: Annotated[str, Field(min_length=1, max_length=300)]
    supplier: Annotated[str, Field(min_length=1, max_length=200)]
    city: Annotated[str, Field(min_length=1, max_length=120)]
    start_date: date
    end_date: date | None = None
    quantity: Annotated[int, Field(ge=1, le=200)]
    unit_basis: Literal["traveller", "room", "group"]
    snapshot_ref: Annotated[str, Field(min_length=1, max_length=100)] | None = None
    fare_rule: FareRuleView

    @model_validator(mode="after")
    def _dates_and_product_agree(self) -> Self:
        if self.end_date is not None and self.end_date < self.start_date:
            raise ValueError("item end_date must not be before start_date")
        if self.fare_rule.product != self.component_type:
            raise ValueError("fare-rule product must match the item component type")
        return self


class DayView(_FrozenModel):
    """One calendar day and its scheduled arrangements."""

    day_index: Annotated[int, Field(ge=1)]
    date: date
    items: tuple[ItemView, ...] = ()


class OptionView(_FrozenModel):
    """One customer-safe option: total plus only the stored tax lines."""

    label: Annotated[str, Field(min_length=1, max_length=80)] | None = None
    quote_id: UUID
    revision_number: Annotated[int, Field(ge=1)]
    total: Money
    tax_lines: tuple[QuoteLine, ...] = ()
    start_date: date
    end_date: date
    travellers: Annotated[int, Field(ge=1, le=200)]
    days: Annotated[tuple[DayView, ...], Field(min_length=1)]
    snapshot_refs: tuple[Annotated[str, Field(min_length=1, max_length=100)], ...] = ()

    @model_validator(mode="after")
    def _customer_safe(self) -> Self:
        if self.total.currency != _INR:
            raise ValueError("a quote document must be in INR")
        if self.total.is_negative():
            raise ValueError("a quote total must not be negative")
        if self.end_date < self.start_date:
            raise ValueError("option end_date must not be before start_date")
        if any(day.date < self.start_date or day.date > self.end_date for day in self.days):
            raise ValueError("every day must fall inside the option dates")

        for line in self.tax_lines:
            if line.kind is not LineKind.TAX:
                raise ValueError("only tax lines may be shown to the customer")
            if line.amount.is_negative():
                raise ValueError("a tax line must not be negative")
        if sum(line.amount.paise for line in self.tax_lines) > self.total.paise:
            raise ValueError("tax lines cannot exceed the total")
        return self


class QuoteDocument(_FrozenModel):
    """A complete, customer-safe quote or multi-option document."""

    id: UUID
    kind: Literal["quote", "group"]
    title: Annotated[str, Field(min_length=1, max_length=200)] | None = None
    prepared_at: AwareDatetime
    expires_at: AwareDatetime
    timezone: Annotated[str, Field(min_length=1, max_length=64)]
    brand: Brand
    options: tuple[OptionView, ...]
    payment_schedule: tuple[PaymentStage, ...]
    terms: Terms
    visa_note: Annotated[str, Field(min_length=1, max_length=2_000)]

    @model_validator(mode="after")
    def _consistent(self) -> Self:
        if not self.options:
            raise ValueError("a quote document needs at least one option")
        if not self.payment_schedule:
            raise ValueError("a quote document needs a payment schedule")
        if self.kind == "group" and self.title is None:
            raise ValueError("a group document needs a title")
        if self.kind == "quote" and self.title is not None:
            raise ValueError("a single quote document cannot carry a group title")
        if self.kind == "group" and any(option.label is None for option in self.options):
            raise ValueError("every option in a group document needs a staff label")
        if self.kind == "quote" and any(option.label is not None for option in self.options):
            raise ValueError("a single quote document cannot label its option")
        if sum(stage.percent_bps for stage in self.payment_schedule) != 10_000:
            raise ValueError("the payment schedule must add up to 100%")
        try:
            ZoneInfo(self.timezone)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError("timezone must be a known IANA zone") from None
        return self

    @property
    def expired(self) -> bool:
        return self.prepared_at >= self.expires_at

    @property
    def reference(self) -> str:
        return str(self.id)


def render_quote_html(document: QuoteDocument) -> str:
    """Render one complete HTML document; all dynamic values are escaped."""

    zone = ZoneInfo(document.timezone)
    options = "".join(_option(option) for option in document.options)
    schedule = "".join(
        f"<tr><td>{_e(stage.label)}</td><td>{_percent(stage.percent_bps)}</td>"
        f"<td>{_e(stage.due)}</td></tr>"
        for stage in document.payment_schedule
    )
    title = (
        f'<p class="group-title">{_e(document.title)}</p>'
        if document.kind == "group" and document.title is not None
        else ""
    )
    expired = (
        f'<p class="expired">This quotation expired on {_instant(document.expires_at, zone)}.</p>'
        if document.expired
        else ""
    )
    gstin = f"<p>GSTIN: {_e(document.brand.gstin)}</p>" if document.brand.gstin is not None else ""
    snapshot_claim = (
        " and supplier snapshot references"
        if any(
            option.snapshot_refs
            or any(item.snapshot_ref for day in option.days for item in day.items)
            for option in document.options
        )
        else ""
    )

    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Quotation {_e(document.reference)}</title>
<style>
@page {{ size: A4; margin: 16mm; }}
body {{ font-family: Arial, sans-serif; color: #222; line-height: 1.45; }}
h1, h2, h3, h4 {{ color: {document.brand.primary_color}; margin: 0 0 0.4em; }}
h1 {{ font-size: 22px; }} h2 {{ font-size: 20px; }} h3 {{ font-size: 17px; }}
h4 {{ font-size: 14px; margin-top: 1.2em; }}
p {{ margin: 0.2em 0; }}
header {{ display: flex; gap: 16px; align-items: center; border-bottom: 3px solid {document.brand.primary_color}; padding-bottom: 12px; }}
header img {{ max-height: 74px; max-width: 180px; }}
.meta {{ color: #555; font-size: 12px; }}
.group-title {{ font-size: 17px; font-weight: bold; margin-top: 0.6em; }}
.expired {{ border: 1px solid #a00; color: #a00; padding: 8px; font-weight: bold; }}
.option {{ border-top: 1px solid #ccc; margin-top: 18px; padding-top: 10px; }}
table {{ border-collapse: collapse; width: 100%; margin-top: 6px; font-size: 12px; }}
th, td {{ border: 1px solid #ccc; padding: 5px 6px; text-align: left; vertical-align: top; }}
thead th {{ background: #f2f2f2; }}
table.price td:last-child, table.price th:last-child {{ text-align: right; white-space: nowrap; }}
tr.total th, tr.total td {{ font-weight: bold; border-top: 2px solid #555; }}
ul {{ margin: 4px 0 4px 18px; padding: 0; }}
.terms {{ white-space: pre-wrap; font-size: 12px; }}
.snapshots {{ color: #555; font-size: 11px; word-break: break-all; }}
footer {{ margin-top: 24px; border-top: 1px solid #ccc; padding-top: 8px; font-size: 11px; color: #555; }}
</style>
</head>
<body>
<header>
<img src="{_e(document.brand.logo_data_uri)}" alt="{_e(document.brand.display_name)} logo">
<div>
<h1>{_e(document.brand.display_name)}</h1>
<p>{_e(document.brand.legal_name)}</p>
<p>{_e(document.brand.registered_address)}</p>
{gstin}
<p>{_e(document.brand.support_email)} · {_e(document.brand.support_phone)}</p>
</div>
</header>
<main>
<h2>Quotation</h2>
<p class="meta">Reference {_e(document.reference)} · Prepared {_instant(document.prepared_at, zone)}
 · Valid until {_instant(document.expires_at, zone)}</p>
{title}
{expired}
{options}
<h3>Payment schedule</h3>
<table>
<thead><tr><th>Stage</th><th>Share</th><th>Due</th></tr></thead>
<tbody>{schedule}</tbody>
</table>
<h3>Terms and conditions (version {_e(document.terms.version)})</h3>
<p class="terms">{_e(document.terms.text)}</p>
<h3>Visa</h3>
<p>Visa requirements: {_e(document.visa_note)}</p>
</main>
<footer>Prepared from the agency's stored price calculation{snapshot_claim}.
Amounts are in Indian rupees.</footer>
</body>
</html>
"""


def _option(option: OptionView) -> str:
    days = "".join(_day(day) for day in option.days)
    fare_rows = "".join(_fare_row(item) for day in option.days for item in day.items)
    tax_rows = "".join(
        f"<tr><td>{_e(line.description)}</td><td>{_money(line.amount)}</td></tr>"
        for line in option.tax_lines
    )
    snapshot = (
        f'<p class="snapshots">Price snapshot references: '
        f"{', '.join(_e(ref) for ref in option.snapshot_refs)}</p>"
        if option.snapshot_refs
        else ""
    )
    traveller_word = "traveller" if option.travellers == 1 else "travellers"
    heading = f"<h3>{_e(option.label)}</h3>\n" if option.label is not None else ""
    return f"""
<section class="option">
{heading}<p class="meta">{_date(option.start_date)} {_DASH} {_date(option.end_date)}
 · {option.travellers} {traveller_word} · Revision {option.revision_number}
 · Quote {_e(str(option.quote_id))}</p>
<ul>{days}</ul>
<h4>Supplier fare conditions</h4>
<table>
<thead><tr><th>Component</th><th>Supplier</th><th>Refundability</th>
<th>Cancellation window</th><th>Change fee</th></tr></thead>
<tbody>{fare_rows}</tbody>
</table>
<h4>Price</h4>
<table class="price">
<tbody>{tax_rows}<tr class="total"><th>Total</th><td>{_money(option.total)}</td></tr></tbody>
</table>
{snapshot}
</section>"""


def _day(day: DayView) -> str:
    if not day.items:
        return (
            f"<li><strong>Day {day.day_index}</strong> ({_date(day.date)}): "
            "no scheduled arrangement.</li>"
        )
    items = "".join(
        f"<li><strong>{_e(item.name)}</strong> {_DASH} {_e(item.supplier)}, "
        f"{_e(item.city)} ({_date(item.start_date)}"
        f"{'' if item.end_date is None else f' to {_date(item.end_date)}'}) "
        f"· {item.quantity} &times; {_e(item.unit_basis)}</li>"
        for item in day.items
    )
    return f"<li><strong>Day {day.day_index}</strong> ({_date(day.date)})<ul>{items}</ul></li>"


def _fare_row(item: ItemView) -> str:
    refundability = "Refundable" if item.fare_rule.refundable else "Non-refundable"
    return (
        f"<tr><td>{_e(item.name)}</td><td>{_e(item.supplier)}</td>"
        f"<td>{refundability}</td><td>{item.fare_rule.cancellation_days} days</td>"
        f"<td>{_money(item.fare_rule.change_fee)}</td></tr>"
    )


def _e(value: object) -> str:
    return html.escape(str(value), quote=True)


def _money(value: Money) -> str:
    return _e(value.format())


def _date(value: date) -> str:
    return value.strftime("%d %b %Y")


def _instant(value: datetime, zone: ZoneInfo) -> str:
    local = value.astimezone(zone)
    name = local.tzname() or zone.key
    return f"{local.strftime('%d %b %Y, %H:%M')} {name}"


def _percent(bps: int) -> str:
    whole, fraction = divmod(bps, 100)
    if fraction == 0:
        return f"{whole}%"
    return f"{whole}.{fraction:02d}".rstrip("0").rstrip(".") + "%"


__all__ = [
    "DayView",
    "FareRuleView",
    "ItemView",
    "OptionView",
    "QuoteDocument",
    "render_quote_html",
]
