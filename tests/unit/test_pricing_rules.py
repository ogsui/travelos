"""Pricing rule loading, placeholder detection and versioning (task 015a).

The golden rule for this slice: a missing owner value is never defaulted. The
loader returns every gap it finds and reports ``NEEDS_CONFIG``, so a pricing run
cannot quietly use an invented margin or tax setting. A complete synthetic file
is used only to prove the mapping and the integer basis-point conversion.
"""

from __future__ import annotations

import hashlib
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml

from travelos.pricing.errors import PricingConfigError
from travelos.pricing.models import (
    CustomerSegment,
    GatewayFeePolicy,
    GstBasis,
    PricingProduct,
    RoundingMode,
    RuleStatus,
)
from travelos.pricing.rules import (
    DEFAULT_RULES_PATH,
    ConfigGap,
    GapReason,
    load_pricing_rules,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SHIPPED_RULES = REPO_ROOT / "config" / "business_rules.yaml"


def complete_rules() -> dict[str, Any]:
    """A synthetic, test-only rule set. Never copy these values to production."""

    margins = {
        product.value: {
            "min_paise": 50_000,
            "min_percent": 3.0,
            "target_percent": 8.0,
            "max_percent": 15.0,
        }
        for product in PricingProduct
    }
    return {
        "version": 7,
        "agency": {"name": None, "gstin": None},
        "pricing": {
            "currency": "INR",
            "rounding": {"mode": "nearest", "step_paise": 1_000},
            "quote_validity_hours": 168,
            "revalidation_tolerance": {"absolute_paise": 50_000, "percent": 2.0},
            "forex_buffer_percent": 1.5,
            "gateway_fee_policy": "passthrough",
            "margins": margins,
            "segments_discount_budget_percent": {segment.value: 2.0 for segment in CustomerSegment},
            "competitor": {
                "max_age_minutes": 1_440,
                "min_comparability": 80,
                "undercut_paise": 10_000,
            },
        },
        "tax": {
            "tax_table_version": 1,
            "gst_basis_by_product": {product.value: "commission" for product in PricingProduct},
            "tcs": {"enabled": False, "rules_ref": None},
        },
    }


def write_rules(tmp_path: Path, data: object, *, name: str = "business_rules.yaml") -> Path:
    path = tmp_path / name
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_the_default_path_is_the_owner_config() -> None:
    assert Path("config/business_rules.yaml") == DEFAULT_RULES_PATH


def test_a_complete_file_loads_into_integer_basis_points(tmp_path: Path) -> None:
    path = write_rules(tmp_path, complete_rules())

    result = load_pricing_rules(path)

    assert result.status is RuleStatus.OK
    assert result.gaps == ()
    assert result.source_hash == hashlib.sha256(path.read_bytes()).hexdigest()
    rules = result.ruleset
    assert rules is not None
    assert rules.version == 7
    assert rules.currency == "INR"
    margin = rules.margins[PricingProduct.FLIGHT_DOMESTIC]
    assert (margin.min_paise, margin.min_bps, margin.target_bps, margin.max_bps) == (
        50_000,
        300,
        800,
        1_500,
    )
    assert rules.rounding.mode is RoundingMode.NEAREST
    assert rules.rounding.step_paise == 1_000
    assert rules.quote_validity_hours == 168
    assert rules.revalidation.absolute_paise == 50_000
    assert rules.revalidation.percent_bps == 200
    assert rules.forex_buffer_bps == 150
    assert rules.gateway_fee_policy is GatewayFeePolicy.PASSTHROUGH
    assert rules.segment_discount_budget_bps[CustomerSegment.NEW] == 200
    assert rules.competitor.max_age == timedelta(minutes=1_440)
    assert rules.competitor.min_comparability == 80
    assert rules.competitor.undercut_paise == 10_000
    assert rules.tax_table_version == 1
    assert rules.gst_basis_by_product[PricingProduct.HOTEL] is GstBasis.COMMISSION
    assert rules.tcs.enabled is False


def test_a_percentage_written_as_a_float_keeps_its_exact_basis_points(tmp_path: Path) -> None:
    data = complete_rules()
    data["pricing"]["margins"]["hotel"]["target_percent"] = 8.55

    result = load_pricing_rules(write_rules(tmp_path, data))

    assert result.ruleset is not None
    assert result.ruleset.margins[PricingProduct.HOTEL].target_bps == 855


def test_the_shipped_config_is_still_a_placeholder() -> None:
    """When the owner fills the real file, this test must be updated deliberately."""

    result = load_pricing_rules(SHIPPED_RULES)

    assert result.status is RuleStatus.NEEDS_CONFIG
    assert result.ruleset is None
    assert result.source_hash is not None
    fields = {gap.field for gap in result.gaps}
    assert "pricing.rounding.mode" in fields
    assert "pricing.margins.flight_domestic.min_paise" in fields
    assert "tax.tax_table_version" in fields


def test_a_missing_file_is_needs_config_not_an_error(tmp_path: Path) -> None:
    result = load_pricing_rules(tmp_path / "absent.yaml")

    assert result.status is RuleStatus.NEEDS_CONFIG
    assert result.ruleset is None
    assert result.source_hash is None
    assert len(result.gaps) == 1
    assert result.gaps[0].field == "config"
    assert result.gaps[0].reason is GapReason.MISSING_FILE


def test_a_missing_pricing_section_is_one_gap(tmp_path: Path) -> None:
    data = complete_rules()
    del data["pricing"]

    result = load_pricing_rules(write_rules(tmp_path, data))

    assert [(gap.field, gap.reason) for gap in result.gaps] == [("pricing", GapReason.MISSING)]


def test_a_missing_tax_section_is_one_gap(tmp_path: Path) -> None:
    data = complete_rules()
    del data["tax"]

    result = load_pricing_rules(write_rules(tmp_path, data))

    assert [(gap.field, gap.reason) for gap in result.gaps] == [("tax", GapReason.MISSING)]


def test_a_single_missing_margin_field_is_reported_precisely(tmp_path: Path) -> None:
    data = complete_rules()
    data["pricing"]["margins"]["flight_domestic"]["min_paise"] = None

    result = load_pricing_rules(write_rules(tmp_path, data))

    assert result.status is RuleStatus.NEEDS_CONFIG
    assert [gap.field for gap in result.gaps] == ["pricing.margins.flight_domestic.min_paise"]


def test_a_missing_segment_budget_is_a_gap(tmp_path: Path) -> None:
    data = complete_rules()
    del data["pricing"]["segments_discount_budget_percent"]["group"]

    result = load_pricing_rules(write_rules(tmp_path, data))

    assert "pricing.segments_discount_budget_percent.group" in {gap.field for gap in result.gaps}


def test_a_missing_gst_basis_is_a_gap(tmp_path: Path) -> None:
    data = complete_rules()
    del data["tax"]["gst_basis_by_product"]["visa"]

    result = load_pricing_rules(write_rules(tmp_path, data))

    assert "tax.gst_basis_by_product.visa" in {gap.field for gap in result.gaps}


def test_tcs_enabled_without_a_reference_is_a_gap(tmp_path: Path) -> None:
    data = complete_rules()
    data["tax"]["tcs"] = {"enabled": True, "rules_ref": None}

    result = load_pricing_rules(write_rules(tmp_path, data))

    assert "tax.tcs.rules_ref" in {gap.field for gap in result.gaps}


def test_a_missing_version_is_a_gap(tmp_path: Path) -> None:
    data = complete_rules()
    del data["version"]

    result = load_pricing_rules(write_rules(tmp_path, data))

    assert [gap.field for gap in result.gaps] == ["version"]


def test_the_source_hash_changes_when_the_file_changes(tmp_path: Path) -> None:
    data = complete_rules()
    path = write_rules(tmp_path, data)
    first = load_pricing_rules(path)
    again = load_pricing_rules(path)
    data["pricing"]["quote_validity_hours"] = 24
    path = write_rules(tmp_path, data)
    changed = load_pricing_rules(path)

    assert first.source_hash == again.source_hash
    assert changed.source_hash != first.source_hash


def test_an_unknown_top_level_section_is_ignored(tmp_path: Path) -> None:
    data = complete_rules()
    data["agency"] = {"name": None, "gstin": None, "extra": "ignored"}
    data["payments"] = {"advance_percent_by_product": {}}

    result = load_pricing_rules(write_rules(tmp_path, data))

    assert result.status is RuleStatus.OK


@pytest.mark.parametrize(
    ("mutate", "field"),
    [
        (
            lambda data: data["pricing"]["margins"]["hotel"].update({"target_percent": 8.555}),
            "hotel.target_percent",
        ),
        (
            lambda data: data["pricing"]["margins"]["hotel"].update({"min_paise": -1}),
            "hotel.min_paise",
        ),
        (
            lambda data: data["pricing"]["margins"]["hotel"].update(
                {"min_percent": 10.0, "target_percent": 8.0}
            ),
            "hotel",
        ),
        (
            lambda data: data["pricing"]["rounding"].update({"step_paise": 0}),
            "rounding.step_paise",
        ),
        (
            lambda data: data["pricing"].update({"currency": "USD"}),
            "currency",
        ),
    ],
)
def test_invalid_values_are_refused_not_defaulted(tmp_path: Path, mutate: Any, field: str) -> None:
    data = complete_rules()
    mutate(data)

    with pytest.raises(PricingConfigError, match=field):
        load_pricing_rules(write_rules(tmp_path, data))


def test_an_unknown_product_key_is_refused(tmp_path: Path) -> None:
    data = complete_rules()
    data["pricing"]["margins"]["insurance"] = {
        "min_paise": 1,
        "min_percent": 1.0,
        "target_percent": 2.0,
        "max_percent": 3.0,
    }

    with pytest.raises(PricingConfigError, match="insurance"):
        load_pricing_rules(write_rules(tmp_path, data))


def test_an_unknown_pricing_key_is_refused(tmp_path: Path) -> None:
    data = complete_rules()
    data["pricing"]["discount_magic"] = 1

    with pytest.raises(PricingConfigError, match="discount_magic"):
        load_pricing_rules(write_rules(tmp_path, data))


def test_a_yaml_syntax_error_is_a_config_error_without_the_file_body(tmp_path: Path) -> None:
    path = tmp_path / "broken.yaml"
    path.write_text("pricing: [unclosed\n", encoding="utf-8")

    with pytest.raises(PricingConfigError, match="YAML") as caught:
        load_pricing_rules(path)

    assert "unclosed" not in str(caught.value)


def test_a_non_mapping_root_is_refused(tmp_path: Path) -> None:
    path = tmp_path / "list.yaml"
    path.write_text("- one\n- two\n", encoding="utf-8")

    with pytest.raises(PricingConfigError, match="mapping"):
        load_pricing_rules(path)


def test_gaps_are_plain_data_without_secret_values() -> None:
    gap = ConfigGap(field="pricing.margins.hotel.min_paise", reason=GapReason.MISSING)

    assert gap.field.startswith("pricing.")
    assert "null" not in gap.detail.lower()
