"""Owner-supplied quotation presentation rules (task 016b2a).

A customer document is only renderable when the owner has supplied the agency
identity, an approved terms version and text, and a complete payment schedule.
Missing values are reported, never defaulted. The logo is read from inside the
config directory and embedded; nothing here writes a customer document.

Every value in these tests is synthetic: the agency, address, email, terms and
payment schedule are engineering fixtures, not owner or legal wording.
"""

from __future__ import annotations

import base64
from pathlib import Path
from typing import Any

import pytest
import yaml

from travelos.quotes.errors import QuotePresentationConfigError
from travelos.quotes.presentation import (
    GapReason,
    PresentationGap,
    PresentationStatus,
    load_quote_presentation,
)

# A 1x1 transparent PNG. Synthetic asset used only to prove image embedding.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)


def base_data() -> dict[str, Any]:
    return {
        "quote_presentation": {
            "timezone": "Asia/Kolkata",
            "brand": {
                "display_name": "Synthetic Travel",
                "legal_name": "Synthetic Travel Private Limited",
                "registered_address": "1 Test Road, Panaji, Goa 403001",
                "gstin": "27AAAAA0000A1Z5",
                "logo_path": "assets/logo.png",
                "primary_color": "#123456",
                "support_email": "help@example.test",
                "support_phone": "+91 90000 00000",
            },
            "terms": {
                "version": "synthetic-v1",
                "text": "Synthetic terms for tests only.",
            },
            "payment_schedule": [
                {"label": "Deposit", "percent": 30, "due": "On booking"},
                {"label": "Balance", "percent": 70, "due": "30 days before travel"},
            ],
            "visa": {"source_note": None},
        },
        "pricing": {"currency": "INR"},
    }


def write_config(
    tmp_path: Path,
    *,
    mutate: Any = None,
    logo: bytes | None = PNG,
) -> Path:
    data = base_data()
    if mutate is not None:
        mutate(data)
    if logo is not None:
        assets = tmp_path / "assets"
        assets.mkdir(exist_ok=True)
        (assets / "logo.png").write_bytes(logo)
    path = tmp_path / "business_rules.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


def test_a_complete_owner_config_loads_and_embeds_the_logo(tmp_path: Path) -> None:
    result = load_quote_presentation(write_config(tmp_path))

    assert result.status is PresentationStatus.OK
    assert result.needs_config is False
    assert result.presentation is not None
    presentation = result.presentation
    assert presentation.brand.display_name == "Synthetic Travel"
    assert presentation.brand.primary_color == "#123456"
    assert presentation.brand.logo_data_uri.startswith("data:image/png;base64,")
    assert [stage.percent_bps for stage in presentation.payment_schedule] == [3000, 7000]
    assert presentation.terms.version == "synthetic-v1"
    assert presentation.timezone == "Asia/Kolkata"
    assert result.source_hash is not None


def test_a_missing_file_needs_config(tmp_path: Path) -> None:
    result = load_quote_presentation(tmp_path / "absent.yaml")

    assert result.status is PresentationStatus.NEEDS_CONFIG
    assert result.needs_config is True
    assert result.presentation is None
    assert result.gaps == (PresentationGap("config", GapReason.MISSING_FILE),)


def test_a_missing_section_needs_config(tmp_path: Path) -> None:
    path = tmp_path / "business_rules.yaml"
    path.write_text(yaml.safe_dump({"pricing": {"currency": "INR"}}), encoding="utf-8")

    result = load_quote_presentation(path)

    assert result.status is PresentationStatus.NEEDS_CONFIG
    assert [(gap.field, gap.reason) for gap in result.gaps] == [
        ("quote_presentation", GapReason.MISSING)
    ]


def test_every_null_brand_field_is_reported_without_inventing_a_value(
    tmp_path: Path,
) -> None:
    def blank_brand(data: dict[str, Any]) -> None:
        for key in data["quote_presentation"]["brand"]:
            data["quote_presentation"]["brand"][key] = None

    result = load_quote_presentation(write_config(tmp_path, mutate=blank_brand))

    assert result.status is PresentationStatus.NEEDS_CONFIG
    assert result.presentation is None
    assert {gap.field for gap in result.gaps} == {
        "quote_presentation.brand.display_name",
        "quote_presentation.brand.legal_name",
        "quote_presentation.brand.registered_address",
        "quote_presentation.brand.logo_path",
        "quote_presentation.brand.primary_color",
        "quote_presentation.brand.support_email",
        "quote_presentation.brand.support_phone",
    }


def test_missing_terms_and_payment_stages_are_reported(tmp_path: Path) -> None:
    def blank(data: dict[str, Any]) -> None:
        data["quote_presentation"]["terms"] = {}
        data["quote_presentation"]["payment_schedule"] = []

    result = load_quote_presentation(write_config(tmp_path, mutate=blank))

    assert result.status is PresentationStatus.NEEDS_CONFIG
    fields = {gap.field for gap in result.gaps}
    assert "quote_presentation.terms.version" in fields
    assert "quote_presentation.terms.text" in fields
    assert "quote_presentation.payment_schedule" in fields


def test_a_schedule_that_does_not_add_up_to_100_percent_is_refused(
    tmp_path: Path,
) -> None:
    def short(data: dict[str, Any]) -> None:
        data["quote_presentation"]["payment_schedule"][1]["percent"] = 60

    with pytest.raises(QuotePresentationConfigError, match="100%"):
        load_quote_presentation(write_config(tmp_path, mutate=short))


def test_a_percent_with_more_than_two_decimals_is_refused(tmp_path: Path) -> None:
    def precise(data: dict[str, Any]) -> None:
        data["quote_presentation"]["payment_schedule"][0]["percent"] = 33.333

    with pytest.raises(QuotePresentationConfigError, match="decimal"):
        load_quote_presentation(write_config(tmp_path, mutate=precise))


def test_two_decimal_percentages_become_exact_basis_points(tmp_path: Path) -> None:
    def thirds(data: dict[str, Any]) -> None:
        data["quote_presentation"]["payment_schedule"] = [
            {"label": "Deposit", "percent": 33.33, "due": "On booking"},
            {"label": "Balance", "percent": 66.67, "due": "Before travel"},
        ]

    result = load_quote_presentation(write_config(tmp_path, mutate=thirds))

    assert result.status is PresentationStatus.OK
    assert result.presentation is not None
    assert [stage.percent_bps for stage in result.presentation.payment_schedule] == [
        3333,
        6667,
    ]


def test_a_malformed_colour_is_refused(tmp_path: Path) -> None:
    def colour(data: dict[str, Any]) -> None:
        data["quote_presentation"]["brand"]["primary_color"] = "blue"

    with pytest.raises(QuotePresentationConfigError, match="primary_color"):
        load_quote_presentation(write_config(tmp_path, mutate=colour))


def test_a_malformed_support_email_is_refused(tmp_path: Path) -> None:
    def email(data: dict[str, Any]) -> None:
        data["quote_presentation"]["brand"]["support_email"] = "not-an-email"

    with pytest.raises(QuotePresentationConfigError, match="support_email"):
        load_quote_presentation(write_config(tmp_path, mutate=email))


def test_an_unknown_timezone_is_refused(tmp_path: Path) -> None:
    def timezone(data: dict[str, Any]) -> None:
        data["quote_presentation"]["timezone"] = "Mars/Olympus"

    with pytest.raises(QuotePresentationConfigError, match="timezone"):
        load_quote_presentation(write_config(tmp_path, mutate=timezone))


def test_a_logo_path_outside_the_config_directory_is_refused(tmp_path: Path) -> None:
    def escape(data: dict[str, Any]) -> None:
        data["quote_presentation"]["brand"]["logo_path"] = "../../outside.png"

    with pytest.raises(QuotePresentationConfigError, match="inside the config directory"):
        load_quote_presentation(write_config(tmp_path, mutate=escape))


def test_a_missing_logo_file_needs_config(tmp_path: Path) -> None:
    def missing(data: dict[str, Any]) -> None:
        data["quote_presentation"]["brand"]["logo_path"] = "assets/missing.png"

    result = load_quote_presentation(write_config(tmp_path, mutate=missing, logo=None))

    assert result.status is PresentationStatus.NEEDS_CONFIG
    assert result.presentation is None
    assert result.gaps == (
        PresentationGap(
            "quote_presentation.brand.logo_path",
            GapReason.MISSING_ASSET,
            "assets/missing.png",
        ),
    )


def test_a_logo_that_is_not_a_png_or_jpeg_is_refused(tmp_path: Path) -> None:
    with pytest.raises(QuotePresentationConfigError, match="PNG or JPEG"):
        load_quote_presentation(write_config(tmp_path, logo=b"not an image"))


def test_an_unknown_brand_key_is_treated_as_a_typo(tmp_path: Path) -> None:
    def typo(data: dict[str, Any]) -> None:
        data["quote_presentation"]["brand"]["logo"] = "assets/logo.png"

    with pytest.raises(QuotePresentationConfigError, match="logo"):
        load_quote_presentation(write_config(tmp_path, mutate=typo))


def test_blank_owner_values_are_reported_as_gaps(tmp_path: Path) -> None:
    def blank(data: dict[str, Any]) -> None:
        data["quote_presentation"]["brand"]["display_name"] = "   "
        data["quote_presentation"]["terms"]["text"] = ""

    result = load_quote_presentation(write_config(tmp_path, mutate=blank))

    assert result.status is PresentationStatus.NEEDS_CONFIG
    fields = {gap.field for gap in result.gaps}
    assert "quote_presentation.brand.display_name" in fields
    assert "quote_presentation.terms.text" in fields


def test_a_malformed_yaml_file_is_refused_without_echoing_its_body(tmp_path: Path) -> None:
    path = tmp_path / "business_rules.yaml"
    path.write_text("quote_presentation: [unclosed", encoding="utf-8")

    with pytest.raises(QuotePresentationConfigError, match="YAML syntax error"):
        load_quote_presentation(path)
