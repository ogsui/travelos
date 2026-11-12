"""Deterministic quote assembly (task 015b2).

``price_quote`` combines converted supplier costs, owner margin rules and an
accountant-approved tax table into floor/target/ceiling/recommended prices. It
never invents a value: incomplete rules, a missing/ambiguous tax row, an
unapproved table, an enabled-but-unmodelled TCS or a missing FX snapshot returns
``NEEDS_CONFIG`` with the reasons instead of a customer price.

Discounts are not applied yet (the approval slice owns them), but an explicit
``proposed_price`` from an owner/agent can be evaluated: below the floor is
``BELOW_FLOOR``, above a trusted competitor ceiling is ``UNCOMPETITIVE``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from travelos.core.money import Money
from travelos.pricing.arithmetic import (
    FxSnapshot,
    MarginBand,
    MarginProblem,
    convert,
    margin_band,
    round_money,
    round_not_below,
)
from travelos.pricing.errors import PricingValidationError
from travelos.pricing.models import (
    CustomerSegment,
    GatewayFeePolicy,
    GstBasis,
    PricingProduct,
    PricingRuleSet,
    RoundingMode,
    RoundingRule,
)
from travelos.pricing.tax import TaxLine, TaxStatus, TaxTable, resolve_tax

_INR: Literal["INR"] = "INR"
_MAX_GAPS = 5


class PriceStatus(StrEnum):
    OK = "ok"
    BELOW_FLOOR = "below_floor"
    UNCOMPETITIVE = "uncompetitive"
    NEEDS_CONFIG = "needs_config"


class _FrozenModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", str_strip_whitespace=True)


class ItemCost(_FrozenModel):
    """One supplier cost, before margin and customer tax."""

    reference: Annotated[str, Field(min_length=1, max_length=100)]
    product: PricingProduct
    net: Money
    supplier_taxes: Money = Field(default_factory=Money.zero)
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _same_currency_and_non_negative(self) -> ItemCost:
        if self.net.currency != self.supplier_taxes.currency:
            raise ValueError("net and supplier_taxes must use the same currency")
        if self.net.paise < 0 or self.supplier_taxes.paise < 0:
            raise ValueError("supplier amounts must not be negative")
        return self


class PriceRequest(_FrozenModel):
    rules: PricingRuleSet
    tax_table: TaxTable
    items: Annotated[tuple[ItemCost, ...], Field(min_length=1)]
    segment: CustomerSegment
    gateway_fee: Money = Field(default_factory=Money.zero)
    competitor_reference: Money | None = None
    proposed_price: Money | None = None
    fx_snapshots: tuple[FxSnapshot, ...] = ()
    now: AwareDatetime

    @model_validator(mode="after")
    def _unique_items_and_inr_amounts(self) -> PriceRequest:
        references = [item.reference for item in self.items]
        if len(references) != len(set(references)):
            raise ValueError("item reference must be unique")
        for name, amount in (
            ("gateway_fee", self.gateway_fee),
            ("competitor_reference", self.competitor_reference),
            ("proposed_price", self.proposed_price),
        ):
            if amount is None:
                continue
            if amount.currency != _INR:
                raise ValueError(f"{name} must be in INR")
            if amount.paise < 0:
                raise ValueError(f"{name} must not be negative")
        return self


class PriceLine(_FrozenModel):
    kind: Literal["supplier", "margin", "tax", "gateway", "rounding", "proposal"]
    label: Annotated[str, Field(min_length=1, max_length=200)]
    amount: Money


class PriceResult(_FrozenModel):
    status: PriceStatus
    currency: Literal["INR"] = _INR
    floor: Money | None = None
    target: Money | None = None
    ceiling: Money | None = None
    recommended: Money | None = None
    customer_price: Money | None = None
    lines: tuple[PriceLine, ...] = ()
    explanation: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    gaps: tuple[str, ...] = ()
    rule_version: Annotated[int, Field(ge=1)]
    tax_table_version: Annotated[int, Field(ge=1)]
    expires_at: AwareDatetime | None = None


@dataclass(frozen=True, slots=True)
class _CostedItem:
    item: ItemCost
    supplier: Money
    band: MarginBand
    fx_note: str | None


@dataclass(frozen=True, slots=True)
class _Level:
    total_paise: int
    tax_lines: tuple[tuple[str, TaxLine], ...]


@dataclass(frozen=True, slots=True)
class _Assembly:
    """The costed price before any customer price is chosen."""

    costed: tuple[_CostedItem, ...]
    target_level: _Level
    floor: int
    target: int
    ceiling: int
    absorbed: bool
    gateway: int
    warnings: tuple[str, ...]
    explanation: tuple[str, ...]


def _assembly(request: PriceRequest) -> tuple[_Assembly | None, PriceResult | None]:
    """Build the three price levels, or the fail-closed result if that is impossible."""

    rules = request.rules
    if rules.tcs.enabled:
        return None, _needs_config(
            request,
            ("tax.tcs: TCS is enabled but no accountant-approved TCS table exists",),
        )

    costed, gaps, fx_notes = _cost_items(request)
    if gaps:
        return None, _needs_config(request, gaps, warnings=fx_notes)

    levels, tax_gaps = _price_levels(request, costed)
    if levels is None:
        return None, _needs_config(request, tax_gaps, warnings=fx_notes)
    floor_level, target_level, cap_level = levels
    absorbed = rules.gateway_fee_policy is GatewayFeePolicy.ABSORB
    gateway = request.gateway_fee.paise
    extra = gateway if absorbed else 0

    floor = floor_level.total_paise + extra
    target = target_level.total_paise + extra
    cap = cap_level.total_paise + extra
    warnings = list(fx_notes)
    explanation = _explain(request, costed, target_level, absorbed, fx_notes)

    if request.competitor_reference is None:
        ceiling = cap
        warnings.append("no trusted competitor price; ceiling is the markup cap")
    else:
        reference = max(0, request.competitor_reference.paise - rules.competitor.undercut_paise)
        ceiling = min(cap, reference)
        warnings.append(
            f"ceiling {ceiling} paise from a trusted competitor reference after the "
            f"{rules.competitor.undercut_paise} paise undercut"
        )

    return (
        _Assembly(
            costed=costed,
            target_level=target_level,
            floor=floor,
            target=target,
            ceiling=ceiling,
            absorbed=absorbed,
            gateway=gateway,
            warnings=tuple(dict.fromkeys(warnings)),
            explanation=tuple(explanation),
        ),
        None,
    )


def price_quote(request: PriceRequest) -> PriceResult:
    assembly, failure = _assembly(request)
    if failure is not None:
        return failure
    assert assembly is not None
    floor = assembly.floor
    target = assembly.target
    ceiling = assembly.ceiling
    gateway = assembly.gateway
    absorbed = assembly.absorbed
    warnings = list(assembly.warnings)

    rounding = request.rules.rounding
    if request.proposed_price is not None:
        proposed_paise = round_money(request.proposed_price, rounding).paise
        recommended = proposed_paise
        if recommended < floor:
            status = PriceStatus.BELOW_FLOOR
            warnings.append("proposed price is below the floor; owner approval is required")
        elif recommended > ceiling:
            status = PriceStatus.UNCOMPETITIVE
            warnings.append("proposed price is above the trusted competitor ceiling")
        else:
            status = PriceStatus.OK
    elif floor > ceiling:
        status = PriceStatus.UNCOMPETITIVE
        recommended = None
        warnings.append(f"floor {floor} paise is above ceiling {ceiling} paise; owner must decide")
    else:
        chosen = min(max(target, floor), ceiling)
        rounded = _round_with_ceiling(chosen, floor, ceiling, rounding)
        if rounded is None:
            status = PriceStatus.UNCOMPETITIVE
            recommended = None
            warnings.append("floor and ceiling cannot both be met at the configured rounding step")
        else:
            recommended = rounded
            status = PriceStatus.OK

    customer: int | None
    lines: tuple[PriceLine, ...]
    if status is PriceStatus.UNCOMPETITIVE:
        customer = None
        recommended = None
        lines = ()
    else:
        assert recommended is not None
        customer = recommended + (gateway if not absorbed else 0)
        lines = _lines(
            request,
            assembly.costed,
            assembly.target_level,
            absorbed,
            gateway,
            customer,
            status,
        )

    return PriceResult(
        status=status,
        floor=Money(floor),
        target=Money(target),
        ceiling=Money(ceiling),
        recommended=Money(recommended) if recommended is not None else None,
        customer_price=Money(customer) if customer is not None else None,
        lines=lines,
        explanation=assembly.explanation,
        warnings=tuple(dict.fromkeys(warnings)),
        rule_version=request.rules.version,
        tax_table_version=request.tax_table.version,
        expires_at=_expires_at(request),
    )


def assemble_owner_price(request: PriceRequest, total: Money) -> PriceResult:
    """Assemble a price the owner chose (task 016c2c).

    The owner total is the final customer total, including a passed-through gateway
    fee. Every cost, margin and tax line still comes from the target assembly, and
    one explicit ``proposal`` line carries the difference, so the lines add up to
    the owner's number exactly. The tax amounts therefore describe the assembled
    price, not a recomputation on the owner's total: the accountant's tax rule for
    an owner-adjusted consideration is not supplied, and this function does not
    invent one. The amount must already sit on the owner's rounding step.
    """

    if total.currency != _INR:
        raise PricingValidationError("owner total must be in INR")
    if total.paise < 0:
        raise PricingValidationError("owner total must not be negative")

    assembly, failure = _assembly(request)
    if failure is not None:
        return failure
    assert assembly is not None

    step = request.rules.rounding.step_paise
    if total.paise % step != 0:
        raise PricingValidationError(f"owner total must be a multiple of {step} paise")

    passed_through = 0 if assembly.absorbed else assembly.gateway
    base = total.paise - passed_through
    if base < 0:
        raise PricingValidationError("owner total is below the passed-through gateway fee")

    if base < assembly.floor:
        status = PriceStatus.BELOW_FLOOR
    elif base > assembly.ceiling:
        status = PriceStatus.UNCOMPETITIVE
    else:
        status = PriceStatus.OK

    lines = _owner_lines(request, assembly, total.paise)
    if sum(line.amount.paise for line in lines) != total.paise:
        raise AssertionError("owner price lines do not add up to the owner total")

    warning = (
        f"owner total {total.paise} paise; the tax lines describe the assembled "
        f"price of {assembly.target} paise, not a recomputation on the owner total"
    )
    return PriceResult(
        status=status,
        floor=Money(assembly.floor),
        target=Money(assembly.target),
        ceiling=Money(assembly.ceiling),
        recommended=Money(base),
        customer_price=total,
        lines=lines,
        explanation=(*assembly.explanation, f"owner total: {total.paise} paise"),
        warnings=tuple(dict.fromkeys((*assembly.warnings, warning))),
        rule_version=request.rules.version,
        tax_table_version=request.tax_table.version,
        expires_at=_expires_at(request),
    )


# ---------------------------------------------------------------------------
# Costs and price levels
# ---------------------------------------------------------------------------


def _cost_items(
    request: PriceRequest,
) -> tuple[tuple[_CostedItem, ...], tuple[str, ...], tuple[str, ...]]:
    costed: list[_CostedItem] = []
    gaps: list[str] = []
    notes: list[str] = []
    snapshots = _latest_snapshots(request)

    for item in request.items:
        if item.expires_at is not None and item.expires_at <= request.now:
            gaps.append(f"item.{item.reference}: expired at {item.expires_at.isoformat()}")
            continue
        base = item.net + item.supplier_taxes
        fx_note: str | None = None
        if base.currency != _INR:
            snapshot = snapshots.get(base.currency)
            if snapshot is None:
                gaps.append(
                    f"fx.{base.currency}: no {base.currency}/{_INR} snapshot for "
                    f"item {item.reference}"
                )
                continue
            if snapshot.quote_currency != _INR:
                gaps.append(
                    f"fx.{base.currency}: snapshot quotes {snapshot.quote_currency}, not INR"
                )
                continue
            if snapshot.fetched_at > request.now:
                gaps.append(f"fx.{base.currency}: snapshot is from the future")
                continue
            base = convert(base, snapshot, buffer_bps=request.rules.forex_buffer_bps)
            fx_note = (
                f"{snapshot.base_currency}/{snapshot.quote_currency} "
                f"{snapshot.numerator}/{snapshot.denominator} with "
                f"{request.rules.forex_buffer_bps} bps buffer, fetched "
                f"{snapshot.fetched_at.isoformat()} from {snapshot.source}"
            )
            if fx_note not in notes:
                notes.append(fx_note)

        band = margin_band(
            base,
            request.rules.margins[item.product],
            request.rules.margin_combination,
        )
        if band is MarginProblem.FLOOR_ABOVE_CAP:
            gaps.append(
                f"pricing.margins.{item.product.value}: fixed floor is above the "
                f"percentage cap at supplier {base.paise} paise"
            )
            continue
        costed.append(_CostedItem(item=item, supplier=base, band=band, fx_note=fx_note))

    return tuple(costed), tuple(gaps), tuple(notes)


def _latest_snapshots(request: PriceRequest) -> dict[str, FxSnapshot]:
    latest: dict[str, FxSnapshot] = {}
    for snapshot in request.fx_snapshots:
        current = latest.get(snapshot.base_currency)
        if current is None or snapshot.fetched_at > current.fetched_at:
            latest[snapshot.base_currency] = snapshot
    return latest


def _price_levels(
    request: PriceRequest,
    costed: tuple[_CostedItem, ...],
) -> tuple[tuple[_Level, _Level, _Level] | None, tuple[str, ...]]:
    """Compute the three levels, returning gaps if any tax row is unusable."""

    levels: list[_Level] = []
    gaps: list[str] = []
    for level in ("minimum", "target", "cap"):
        total = 0
        tax_lines: list[tuple[str, TaxLine]] = []
        for costed_item in costed:
            margin = _band_value(costed_item.band, level)
            total += costed_item.supplier.paise + margin
            basis = request.rules.gst_basis_by_product[costed_item.item.product]
            base_paise = (
                margin if basis is GstBasis.COMMISSION else costed_item.supplier.paise + margin
            )
            resolution = resolve_tax(
                request.tax_table,
                basis=basis,
                product=costed_item.item.product,
                base=Money(base_paise),
            )
            if resolution.status is not TaxStatus.OK or resolution.line is None:
                gaps.append(
                    f"tax.{costed_item.item.product.value}."
                    f"{costed_item.item.reference}.{level}: {resolution.detail}"
                )
                continue
            total += resolution.line.tax.paise
            tax_lines.append((costed_item.item.reference, resolution.line))
        levels.append(_Level(total_paise=total, tax_lines=tuple(tax_lines)))
    if gaps:
        return None, tuple(gaps)
    return (levels[0], levels[1], levels[2]), ()


def _band_value(band: MarginBand, level: str) -> int:
    if level == "minimum":
        return band.minimum.paise
    if level == "target":
        return band.target.paise
    return band.cap.paise


# ---------------------------------------------------------------------------
# Presentation
# ---------------------------------------------------------------------------


def _round_with_ceiling(
    amount: int,
    floor: int,
    ceiling: int,
    rule: RoundingRule,
) -> int | None:
    rounded = round_not_below(Money(amount), Money(floor), rule).paise
    if rounded <= ceiling:
        return rounded
    below = round_money(
        Money(ceiling), RoundingRule(mode=RoundingMode.DOWN, step_paise=rule.step_paise)
    ).paise
    if below >= floor:
        return below
    return None


def _assembled_lines(
    request: PriceRequest,
    costed: tuple[_CostedItem, ...],
    target_level: _Level,
    absorbed: bool,
    gateway: int,
) -> list[PriceLine]:
    """Every supplier, margin, tax and gateway line of the target assembly."""

    lines: list[PriceLine] = []
    for costed_item in costed:
        item = costed_item.item
        lines.append(
            PriceLine(
                kind="supplier",
                label=f"{item.reference}: supplier cost",
                amount=costed_item.supplier,
            )
        )
        if costed_item.fx_note is not None:
            lines.append(
                PriceLine(
                    kind="supplier",
                    label=f"{item.reference}: FX {costed_item.fx_note}",
                    amount=Money(0),
                )
            )
        margin = _band_value(costed_item.band, "target")
        lines.append(
            PriceLine(
                kind="margin",
                label=(
                    f"{item.reference}: margin {_percent(request.rules.margins[item.product].target_bps)}"
                ),
                amount=Money(margin),
            )
        )
    for reference, tax_line in target_level.tax_lines:
        lines.append(
            PriceLine(
                kind="tax",
                label=(
                    f"{reference}: GST {_percent(tax_line.rate_bps)} on "
                    f"{tax_line.base.paise} ({tax_line.reference})"
                ),
                amount=tax_line.tax,
            )
        )
    if gateway:
        lines.append(
            PriceLine(
                kind="gateway",
                label=("gateway fee (absorbed)" if absorbed else "gateway fee (passed through)"),
                amount=Money(gateway),
            )
        )
    return lines


def _lines(
    request: PriceRequest,
    costed: tuple[_CostedItem, ...],
    target_level: _Level,
    absorbed: bool,
    gateway: int,
    customer: int,
    status: PriceStatus,
) -> tuple[PriceLine, ...]:
    if status is PriceStatus.UNCOMPETITIVE:
        return ()
    lines = _assembled_lines(request, costed, target_level, absorbed, gateway)
    composed = sum(line.amount.paise for line in lines)
    if status is PriceStatus.BELOW_FLOOR:
        lines.append(
            PriceLine(
                kind="proposal",
                label="owner/agent proposal (difference from the assembled price)",
                amount=Money(customer - composed),
            )
        )
    else:
        lines.append(
            PriceLine(
                kind="rounding",
                label=f"rounding to {request.rules.rounding.step_paise} paise steps",
                amount=Money(customer - composed),
            )
        )
    return tuple(lines)


def _owner_lines(
    request: PriceRequest,
    assembly: _Assembly,
    customer: int,
) -> tuple[PriceLine, ...]:
    """The assembly's lines plus one explicit difference up to the owner's total."""

    lines = _assembled_lines(
        request,
        assembly.costed,
        assembly.target_level,
        assembly.absorbed,
        assembly.gateway,
    )
    composed = sum(line.amount.paise for line in lines)
    lines.append(
        PriceLine(
            kind="proposal",
            label="owner total (difference from the assembled price)",
            amount=Money(customer - composed),
        )
    )
    return tuple(lines)


def _explain(
    request: PriceRequest,
    costed: tuple[_CostedItem, ...],
    target_level: _Level,
    absorbed: bool,
    fx_notes: tuple[str, ...],
) -> list[str]:
    explanation: list[str] = []
    for costed_item in costed:
        item = costed_item.item
        suffix = f" converted at {costed_item.fx_note}" if costed_item.fx_note else ""
        explanation.append(
            f"{item.reference} ({item.product.value}): supplier "
            f"{costed_item.supplier.paise} paise{suffix}"
        )
        margin_rule = request.rules.margins[item.product]
        explanation.append(
            f"{item.reference}: target margin "
            f"{_band_value(costed_item.band, 'target')} paise "
            f"({_percent(margin_rule.target_bps)} of supplier, "
            f"{request.rules.margin_combination.value} combination)"
        )
    for reference, tax_line in target_level.tax_lines:
        explanation.append(
            f"{reference}: tax {tax_line.tax.paise} paise at "
            f"{_percent(tax_line.rate_bps)} on {tax_line.base.paise} paise "
            f"(row {tax_line.reference}, table {tax_line.table_version})"
        )
    if request.gateway_fee.paise:
        explanation.append(
            f"gateway fee {request.gateway_fee.paise} paise "
            f"({'absorbed' if absorbed else 'passed through'})"
        )
    explanation.append(
        f"rounding: {request.rules.rounding.mode.value} to "
        f"{request.rules.rounding.step_paise} paise steps"
    )
    budget = request.rules.segment_discount_budget_bps[request.segment]
    explanation.append(
        f"segment {request.segment.value}: {_percent(budget)} discount budget "
        "(not applied in this slice)"
    )
    explanation.extend(f"fx: {note}" for note in fx_notes)
    return explanation


def _expires_at(request: PriceRequest) -> AwareDatetime:
    validity = request.now + timedelta(hours=request.rules.quote_validity_hours)
    expiries = [validity]
    expiries.extend(item.expires_at for item in request.items if item.expires_at is not None)
    return min(expiries)


def _percent(basis_points: int) -> str:
    whole, fraction = divmod(basis_points, 100)
    if fraction == 0:
        return f"{whole}%"
    return f"{whole}.{fraction:02d}%"


def _needs_config(
    request: PriceRequest,
    gaps: tuple[str, ...],
    *,
    warnings: tuple[str, ...] = (),
) -> PriceResult:
    return PriceResult(
        status=PriceStatus.NEEDS_CONFIG,
        gaps=tuple(gaps[:_MAX_GAPS]),
        warnings=tuple(dict.fromkeys(warnings)),
        rule_version=request.rules.version,
        tax_table_version=request.tax_table.version,
    )
