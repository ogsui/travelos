"""Load the owner's pricing rules and report every missing value (task 015a).

The loader never invents a value. A field left ``null`` in
``config/business_rules.yaml`` becomes a :class:`ConfigGap`, and the whole
ruleset is reported as ``NEEDS_CONFIG``; a pricing run must then refuse to quote.
Malformed values are different: they raise :class:`PricingConfigError` naming the
field, because a typo must not look like an unfinished placeholder.

Only ``version``, ``pricing`` and ``tax`` are read here. Other sections of the
file belong to other modules and are ignored so that one owner file can serve the
whole system.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import timedelta
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

import yaml
from pydantic import ValidationError

from travelos.pricing.errors import PricingConfigError
from travelos.pricing.models import (
    CompetitorPolicy,
    CustomerSegment,
    MarginRule,
    PricingProduct,
    PricingRuleSet,
    RawBusinessRules,
    RawMarginRule,
    RawPricingSection,
    RawTaxSection,
    RevalidationTolerance,
    RoundingRule,
    RuleStatus,
    TcsPolicy,
)

DEFAULT_RULES_PATH = Path("config/business_rules.yaml")

_BPS_PER_PERCENT = Decimal(100)


class GapReason(StrEnum):
    """Why a rule could not be used. Both reasons mean ``NEEDS_CONFIG``."""

    MISSING = "missing"
    MISSING_FILE = "missing_file"


@dataclass(frozen=True, slots=True)
class ConfigGap:
    """One owner value that is absent. Carries no price and no secret."""

    field: str
    reason: GapReason
    detail: str = ""


@dataclass(frozen=True, slots=True)
class RulesetLoadResult:
    """Outcome of reading one rule file. ``ruleset`` is set only when complete."""

    status: RuleStatus
    gaps: tuple[ConfigGap, ...] = ()
    ruleset: PricingRuleSet | None = None
    source_hash: str | None = None

    @property
    def needs_config(self) -> bool:
        return self.status is RuleStatus.NEEDS_CONFIG


def load_pricing_rules(path: Path | str = DEFAULT_RULES_PATH) -> RulesetLoadResult:
    """Read ``path`` and either return complete rules or the list of gaps."""

    rules_path = Path(path)
    try:
        payload = rules_path.read_bytes()
    except FileNotFoundError:
        return RulesetLoadResult(
            status=RuleStatus.NEEDS_CONFIG,
            gaps=(ConfigGap("config", GapReason.MISSING_FILE),),
        )

    try:
        document = yaml.safe_load(payload)
    except yaml.YAMLError:
        # Never echo the file body: it may grow secrets later.
        raise PricingConfigError(f"{rules_path.name}: YAML syntax error") from None

    if document is None:
        document = {}
    if not isinstance(document, dict):
        raise PricingConfigError(f"{rules_path.name}: root must be a mapping")
    try:
        raw = RawBusinessRules.model_validate(document)
    except ValidationError as exc:
        raise PricingConfigError(_pydantic_summary(exc)) from None

    source_hash = hashlib.sha256(payload).hexdigest()
    gaps = _collect_gaps(raw)
    if gaps:
        return RulesetLoadResult(
            status=RuleStatus.NEEDS_CONFIG,
            gaps=gaps,
            source_hash=source_hash,
        )
    return RulesetLoadResult(
        status=RuleStatus.OK,
        ruleset=_build_ruleset(raw),
        source_hash=source_hash,
    )


def _pydantic_summary(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(piece) for piece in error["loc"])
        message = str(error["msg"])
        parts.append(f"{location}: {message}" if location else message)
    return "invalid pricing rules: " + "; ".join(parts[:5])


def _require[T](value: T | None, path: str) -> T:
    if value is None:  # pragma: no cover - gaps are checked before this
        raise PricingConfigError(f"{path}: value is required")
    return value


def _bps(percent: Decimal, path: str) -> int:
    """Convert a percent with at most two decimals to integer basis points."""

    scaled = percent * _BPS_PER_PERCENT
    if scaled != scaled.to_integral_value():
        raise PricingConfigError(f"{path}: percentage must have at most two decimals")
    return int(scaled)


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------


def _collect_gaps(raw: RawBusinessRules) -> tuple[ConfigGap, ...]:
    gaps: list[ConfigGap] = []
    if raw.version is None:
        gaps.append(ConfigGap("version", GapReason.MISSING))
    if raw.pricing is None:
        gaps.append(ConfigGap("pricing", GapReason.MISSING))
    else:
        gaps.extend(_pricing_gaps(raw.pricing))
    if raw.tax is None:
        gaps.append(ConfigGap("tax", GapReason.MISSING))
    else:
        gaps.extend(_tax_gaps(raw.tax))
    return tuple(gaps)


def _add(gaps: list[ConfigGap], path: str, value: object | None) -> None:
    if value is None:
        gaps.append(ConfigGap(path, GapReason.MISSING))


def _pricing_gaps(pricing: RawPricingSection) -> list[ConfigGap]:
    gaps: list[ConfigGap] = []
    _add(gaps, "pricing.currency", pricing.currency)
    _add(gaps, "pricing.rounding.mode", pricing.rounding.mode)
    _add(gaps, "pricing.rounding.step_paise", pricing.rounding.step_paise)
    _add(gaps, "pricing.quote_validity_hours", pricing.quote_validity_hours)
    _add(
        gaps,
        "pricing.revalidation_tolerance.absolute_paise",
        pricing.revalidation_tolerance.absolute_paise,
    )
    _add(
        gaps,
        "pricing.revalidation_tolerance.percent",
        pricing.revalidation_tolerance.percent,
    )
    _add(gaps, "pricing.forex_buffer_percent", pricing.forex_buffer_percent)
    _add(gaps, "pricing.gateway_fee_policy", pricing.gateway_fee_policy)
    _add(gaps, "pricing.margin_combination", pricing.margin_combination)

    for product in PricingProduct:
        prefix = f"pricing.margins.{product.value}"
        margin = pricing.margins.get(product)
        if margin is None:
            gaps.append(ConfigGap(prefix, GapReason.MISSING))
            continue
        gaps.extend(_margin_gaps(margin, prefix))

    for segment in CustomerSegment:
        _add(
            gaps,
            f"pricing.segments_discount_budget_percent.{segment.value}",
            pricing.segments_discount_budget_percent.get(segment),
        )

    _add(gaps, "pricing.competitor.max_age_minutes", pricing.competitor.max_age_minutes)
    _add(
        gaps,
        "pricing.competitor.min_comparability",
        pricing.competitor.min_comparability,
    )
    _add(gaps, "pricing.competitor.undercut_paise", pricing.competitor.undercut_paise)
    return gaps


def _margin_gaps(margin: RawMarginRule, prefix: str) -> list[ConfigGap]:
    gaps: list[ConfigGap] = []
    _add(gaps, f"{prefix}.min_paise", margin.min_paise)
    _add(gaps, f"{prefix}.min_percent", margin.min_percent)
    _add(gaps, f"{prefix}.target_percent", margin.target_percent)
    _add(gaps, f"{prefix}.max_percent", margin.max_percent)
    return gaps


def _tax_gaps(tax: RawTaxSection) -> list[ConfigGap]:
    gaps: list[ConfigGap] = []
    _add(gaps, "tax.tax_table_version", tax.tax_table_version)
    for product in PricingProduct:
        _add(
            gaps,
            f"tax.gst_basis_by_product.{product.value}",
            tax.gst_basis_by_product.get(product),
        )
    _add(gaps, "tax.tcs.enabled", tax.tcs.enabled)
    if tax.tcs.enabled and not tax.tcs.rules_ref:
        gaps.append(ConfigGap("tax.tcs.rules_ref", GapReason.MISSING))
    return gaps


# ---------------------------------------------------------------------------
# Complete ruleset
# ---------------------------------------------------------------------------


def _build_ruleset(raw: RawBusinessRules) -> PricingRuleSet:
    pricing = _require(raw.pricing, "pricing")
    tax = _require(raw.tax, "tax")

    margins = {
        product: _margin(
            _require(pricing.margins.get(product), f"pricing.margins.{product.value}"),
            f"pricing.margins.{product.value}",
        )
        for product in PricingProduct
    }
    segment_budgets = {
        segment: _bps(
            _require(
                pricing.segments_discount_budget_percent.get(segment),
                f"pricing.segments_discount_budget_percent.{segment.value}",
            ),
            f"pricing.segments_discount_budget_percent.{segment.value}",
        )
        for segment in CustomerSegment
    }
    gst_bases = {
        product: _require(
            tax.gst_basis_by_product.get(product),
            f"tax.gst_basis_by_product.{product.value}",
        )
        for product in PricingProduct
    }

    try:
        return PricingRuleSet(
            version=_require(raw.version, "version"),
            currency=_require(pricing.currency, "pricing.currency"),
            rounding=RoundingRule(
                mode=_require(pricing.rounding.mode, "pricing.rounding.mode"),
                step_paise=_require(pricing.rounding.step_paise, "pricing.rounding.step_paise"),
            ),
            quote_validity_hours=_require(
                pricing.quote_validity_hours, "pricing.quote_validity_hours"
            ),
            revalidation=RevalidationTolerance(
                absolute_paise=_require(
                    pricing.revalidation_tolerance.absolute_paise,
                    "pricing.revalidation_tolerance.absolute_paise",
                ),
                percent_bps=_bps(
                    _require(
                        pricing.revalidation_tolerance.percent,
                        "pricing.revalidation_tolerance.percent",
                    ),
                    "pricing.revalidation_tolerance.percent",
                ),
            ),
            forex_buffer_bps=_bps(
                _require(pricing.forex_buffer_percent, "pricing.forex_buffer_percent"),
                "pricing.forex_buffer_percent",
            ),
            gateway_fee_policy=_require(pricing.gateway_fee_policy, "pricing.gateway_fee_policy"),
            margin_combination=_require(pricing.margin_combination, "pricing.margin_combination"),
            margins=margins,
            segment_discount_budget_bps=segment_budgets,
            competitor=CompetitorPolicy(
                max_age=timedelta(
                    minutes=_require(
                        pricing.competitor.max_age_minutes,
                        "pricing.competitor.max_age_minutes",
                    )
                ),
                min_comparability=_require(
                    pricing.competitor.min_comparability,
                    "pricing.competitor.min_comparability",
                ),
                undercut_paise=_require(
                    pricing.competitor.undercut_paise,
                    "pricing.competitor.undercut_paise",
                ),
            ),
            tax_table_version=_require(tax.tax_table_version, "tax.tax_table_version"),
            gst_basis_by_product=gst_bases,
            tcs=TcsPolicy(
                enabled=_require(tax.tcs.enabled, "tax.tcs.enabled"),
                rules_ref=tax.tcs.rules_ref,
            ),
        )
    except ValidationError as exc:
        raise PricingConfigError(_pydantic_summary(exc)) from None


def _margin(margin: RawMarginRule, prefix: str) -> MarginRule:
    try:
        return MarginRule(
            min_paise=_require(margin.min_paise, f"{prefix}.min_paise"),
            min_bps=_bps(
                _require(margin.min_percent, f"{prefix}.min_percent"),
                f"{prefix}.min_percent",
            ),
            target_bps=_bps(
                _require(margin.target_percent, f"{prefix}.target_percent"),
                f"{prefix}.target_percent",
            ),
            max_bps=_bps(
                _require(margin.max_percent, f"{prefix}.max_percent"),
                f"{prefix}.max_percent",
            ),
        )
    except ValidationError as exc:
        raise PricingConfigError(f"{prefix}: {_pydantic_summary(exc)}") from None
