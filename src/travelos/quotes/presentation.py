"""Owner-supplied quotation presentation rules (task 016b2a).

A customer document is only renderable when the owner has supplied the agency
identity, an approved terms version and text, and a complete payment schedule.
Missing values are reported as :class:`PresentationGap` and the status is
``NEEDS_CONFIG``; the renderer never invents legal wording, brand colours or
payment terms.

The logo is read from a path inside the configuration directory and embedded as a
data URI, so the rendered HTML is self-contained and cannot reference a local
file. A path outside that directory is refused rather than followed.

Only the ``quote_presentation`` section is read here. The same owner file also
holds pricing, tax and outreach settings, which belong to other modules.
"""

from __future__ import annotations

import base64
import hashlib
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Annotated
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import yaml
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    ValidationError,
)

from travelos.quotes.errors import QuotePresentationConfigError

DEFAULT_PRESENTATION_PATH = Path("config/business_rules.yaml")
_PRESENTATION_KEY = "quote_presentation"
_BPS_PER_PERCENT = Decimal(100)
_MAX_LOGO_BYTES = 2 * 1024 * 1024
_PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
_JPEG_MAGIC = b"\xff\xd8\xff"


class GapReason(StrEnum):
    """Why an owner value or asset could not be used. All mean ``NEEDS_CONFIG``."""

    MISSING = "missing"
    MISSING_FILE = "missing_file"
    MISSING_ASSET = "missing_asset"


class PresentationStatus(StrEnum):
    """Whether the owner's presentation rules are complete enough to render."""

    OK = "ok"
    NEEDS_CONFIG = "needs_config"


@dataclass(frozen=True, slots=True)
class PresentationGap:
    """One owner value or asset that is absent. Carries no customer data."""

    field: str
    reason: GapReason
    detail: str = ""


@dataclass(frozen=True, slots=True)
class PresentationLoadResult:
    """Outcome of reading one owner file."""

    status: PresentationStatus
    gaps: tuple[PresentationGap, ...] = ()
    presentation: QuotePresentation | None = None
    source_hash: str | None = None

    @property
    def needs_config(self) -> bool:
        return self.status is PresentationStatus.NEEDS_CONFIG


class _StrictModel(BaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        str_strip_whitespace=True,
        validate_default=True,
    )


def _percent_from_number(value: object) -> object:
    """Keep a YAML float such as 33.33 exact before Decimal validation."""

    if isinstance(value, float):
        return Decimal(str(value))
    return value


Percent = Annotated[
    Decimal,
    BeforeValidator(_percent_from_number),
    Field(gt=0, le=100, decimal_places=2),
]


# ---------------------------------------------------------------------------
# Raw configuration (placeholder-friendly)
# ---------------------------------------------------------------------------


class RawBrand(_StrictModel):
    display_name: str | None = None
    legal_name: str | None = None
    registered_address: str | None = None
    gstin: str | None = None
    logo_path: str | None = None
    primary_color: str | None = None
    support_email: str | None = None
    support_phone: str | None = None


class RawTerms(_StrictModel):
    version: str | None = None
    text: str | None = None


class RawPaymentStage(_StrictModel):
    label: str | None = None
    percent: Percent | None = None
    due: str | None = None


class RawVisaNotes(_StrictModel):
    # Optional: when absent the document says "To be confirmed", never a guess.
    source_note: str | None = None


class RawQuotePresentation(_StrictModel):
    timezone: str | None = None
    brand: RawBrand = Field(default_factory=RawBrand)
    terms: RawTerms = Field(default_factory=RawTerms)
    payment_schedule: list[RawPaymentStage] = Field(default_factory=list)
    visa: RawVisaNotes = Field(default_factory=RawVisaNotes)


# ---------------------------------------------------------------------------
# Complete domain rules (no placeholders)
# ---------------------------------------------------------------------------


class Brand(_StrictModel):
    """The agency identity printed on every customer document."""

    display_name: Annotated[str, Field(min_length=1, max_length=200)]
    legal_name: Annotated[str, Field(min_length=1, max_length=200)]
    registered_address: Annotated[str, Field(min_length=1, max_length=500)]
    gstin: Annotated[str, Field(min_length=5, max_length=20)] | None = None
    logo_data_uri: Annotated[
        str,
        Field(pattern=r"^data:image/(png|jpeg);base64,[A-Za-z0-9+/]+=*$"),
    ]
    primary_color: Annotated[str, Field(pattern=r"^#[0-9A-F]{6}$")]
    support_email: Annotated[
        str,
        Field(
            min_length=3,
            max_length=200,
            pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$",
        ),
    ]
    support_phone: Annotated[str, Field(min_length=3, max_length=40)]


class Terms(_StrictModel):
    """Owner/legal approved terms; the version makes the shown text traceable."""

    version: Annotated[str, Field(min_length=1, max_length=80)]
    text: Annotated[str, Field(min_length=1, max_length=20_000)]


class PaymentStage(_StrictModel):
    """One owner-supplied stage of the payment schedule, in basis points."""

    label: Annotated[str, Field(min_length=1, max_length=80)]
    percent_bps: Annotated[int, Field(gt=0, le=10_000)]
    due: Annotated[str, Field(min_length=1, max_length=120)]


class VisaNotes(_StrictModel):
    """An owner-supplied visa note, or nothing so the document says pending."""

    source_note: Annotated[str, Field(min_length=1, max_length=2_000)] | None = None


class QuotePresentation(_StrictModel):
    """Complete presentation rules, ready to render."""

    timezone: Annotated[str, Field(min_length=1, max_length=64)]
    brand: Brand
    terms: Terms
    payment_schedule: Annotated[tuple[PaymentStage, ...], Field(min_length=1)]
    visa: VisaNotes = Field(default_factory=VisaNotes)


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------


def load_quote_presentation(
    path: Path | str = DEFAULT_PRESENTATION_PATH,
) -> PresentationLoadResult:
    """Read the owner file and either return complete rules or the gaps."""

    config_path = Path(path)
    try:
        payload = config_path.read_bytes()
    except FileNotFoundError:
        return PresentationLoadResult(
            status=PresentationStatus.NEEDS_CONFIG,
            gaps=(PresentationGap("config", GapReason.MISSING_FILE),),
        )

    try:
        document = yaml.safe_load(payload)
    except yaml.YAMLError:
        # Never echo the file body: it may grow secrets later.
        raise QuotePresentationConfigError(f"{config_path.name}: YAML syntax error") from None

    if document is None:
        document = {}
    if not isinstance(document, dict):
        raise QuotePresentationConfigError(f"{config_path.name}: root must be a mapping")

    section = document.get(_PRESENTATION_KEY)
    if section is None:
        return PresentationLoadResult(
            status=PresentationStatus.NEEDS_CONFIG,
            gaps=(PresentationGap(_PRESENTATION_KEY, GapReason.MISSING),),
            source_hash=hashlib.sha256(payload).hexdigest(),
        )

    try:
        raw = RawQuotePresentation.model_validate(section)
    except ValidationError as exc:
        raise QuotePresentationConfigError(_pydantic_summary(exc)) from None

    base_dir = config_path.parent
    gaps = _collect_gaps(raw, base_dir)
    source_hash = hashlib.sha256(payload).hexdigest()
    if gaps:
        return PresentationLoadResult(
            status=PresentationStatus.NEEDS_CONFIG,
            gaps=gaps,
            source_hash=source_hash,
        )

    try:
        presentation = _build(raw, base_dir)
    except ValidationError as exc:
        raise QuotePresentationConfigError(_pydantic_summary(exc)) from None
    return PresentationLoadResult(
        status=PresentationStatus.OK,
        presentation=presentation,
        source_hash=source_hash,
    )


def _pydantic_summary(exc: ValidationError) -> str:
    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(piece) for piece in error["loc"])
        message = str(error["msg"])
        parts.append(f"{location}: {message}" if location else message)
    return "invalid quote presentation: " + "; ".join(parts[:5])


def _collect_gaps(raw: RawQuotePresentation, base_dir: Path) -> tuple[PresentationGap, ...]:
    gaps: list[PresentationGap] = []
    _add(gaps, "quote_presentation.timezone", raw.timezone)

    for field in (
        "display_name",
        "legal_name",
        "registered_address",
        "logo_path",
        "primary_color",
        "support_email",
        "support_phone",
    ):
        _add(gaps, f"quote_presentation.brand.{field}", getattr(raw.brand, field))

    _add(gaps, "quote_presentation.terms.version", raw.terms.version)
    _add(gaps, "quote_presentation.terms.text", raw.terms.text)

    if not raw.payment_schedule:
        gaps.append(
            PresentationGap(
                "quote_presentation.payment_schedule",
                GapReason.MISSING,
                "at least one stage is required",
            )
        )
    else:
        for index, stage in enumerate(raw.payment_schedule):
            prefix = f"quote_presentation.payment_schedule[{index}]"
            _add(gaps, f"{prefix}.label", stage.label)
            _add(gaps, f"{prefix}.percent", stage.percent)
            _add(gaps, f"{prefix}.due", stage.due)

    if raw.brand.logo_path is not None:
        logo = _safe_logo_path(base_dir, raw.brand.logo_path)
        if not logo.is_file():
            gaps.append(
                PresentationGap(
                    "quote_presentation.brand.logo_path",
                    GapReason.MISSING_ASSET,
                    raw.brand.logo_path,
                )
            )
    return tuple(gaps)


def _add(gaps: list[PresentationGap], path: str, value: object | None) -> None:
    if value is None or (isinstance(value, str) and not value.strip()):
        gaps.append(PresentationGap(path, GapReason.MISSING))


def _safe_logo_path(base_dir: Path, configured: str) -> Path:
    root = base_dir.resolve()
    candidate = (root / configured).resolve()
    if not candidate.is_relative_to(root):
        raise QuotePresentationConfigError(
            "quote_presentation.brand.logo_path must stay inside the config directory"
        )
    return candidate


def _build(raw: RawQuotePresentation, base_dir: Path) -> QuotePresentation:
    timezone = _require(raw.timezone, "quote_presentation.timezone")
    brand = raw.brand
    logo_path = _require(brand.logo_path, "quote_presentation.brand.logo_path")
    logo_data_uri = _logo_data_uri(_safe_logo_path(base_dir, logo_path))

    stages = tuple(
        PaymentStage(
            label=_require(stage.label, f"payment_schedule[{index}].label"),
            percent_bps=_bps(
                _require(stage.percent, f"payment_schedule[{index}].percent"),
                f"payment_schedule[{index}].percent",
            ),
            due=_require(stage.due, f"payment_schedule[{index}].due"),
        )
        for index, stage in enumerate(raw.payment_schedule)
    )
    if sum(stage.percent_bps for stage in stages) != 10_000:
        raise QuotePresentationConfigError(
            "quote_presentation.payment_schedule must add up to 100%"
        )

    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise QuotePresentationConfigError(
            f"quote_presentation.timezone: unknown timezone {timezone!r}"
        ) from None

    return QuotePresentation(
        timezone=timezone,
        brand=Brand(
            display_name=_require(brand.display_name, "quote_presentation.brand.display_name"),
            legal_name=_require(brand.legal_name, "quote_presentation.brand.legal_name"),
            registered_address=_require(
                brand.registered_address,
                "quote_presentation.brand.registered_address",
            ),
            gstin=brand.gstin or None,
            logo_data_uri=logo_data_uri,
            primary_color=_require(
                brand.primary_color,
                "quote_presentation.brand.primary_color",
            ).upper(),
            support_email=_require(
                brand.support_email,
                "quote_presentation.brand.support_email",
            ),
            support_phone=_require(
                brand.support_phone,
                "quote_presentation.brand.support_phone",
            ),
        ),
        terms=Terms(
            version=_require(raw.terms.version, "quote_presentation.terms.version"),
            text=_require(raw.terms.text, "quote_presentation.terms.text"),
        ),
        payment_schedule=stages,
        visa=VisaNotes(source_note=raw.visa.source_note or None),
    )


def _require[T](value: T | None, path: str) -> T:
    if value is None:  # pragma: no cover - gaps are checked before this
        raise QuotePresentationConfigError(f"{path}: value is required")
    return value


def _bps(percent: Decimal, path: str) -> int:
    """Convert a percent with at most two decimals to integer basis points."""

    scaled = percent * _BPS_PER_PERCENT
    if scaled != scaled.to_integral_value():
        raise QuotePresentationConfigError(f"{path}: percentage must have at most two decimals")
    return int(scaled)


def _logo_data_uri(path: Path) -> str:
    try:
        payload = path.read_bytes()
    except OSError:
        raise QuotePresentationConfigError(
            "quote_presentation.brand.logo_path: asset was not found"
        ) from None
    if not payload:
        raise QuotePresentationConfigError("quote_presentation.brand.logo_path: asset is empty")
    if len(payload) > _MAX_LOGO_BYTES:
        raise QuotePresentationConfigError(
            "quote_presentation.brand.logo_path: asset exceeds 2 MiB"
        )
    if payload.startswith(_PNG_MAGIC):
        mime = "png"
    elif payload.startswith(_JPEG_MAGIC):
        mime = "jpeg"
    else:
        raise QuotePresentationConfigError(
            "quote_presentation.brand.logo_path: asset must be a PNG or JPEG image"
        )
    encoded = base64.b64encode(payload).decode("ascii")
    return f"data:image/{mime};base64,{encoded}"


__all__ = [
    "DEFAULT_PRESENTATION_PATH",
    "Brand",
    "GapReason",
    "PaymentStage",
    "PresentationGap",
    "PresentationLoadResult",
    "PresentationStatus",
    "QuotePresentation",
    "Terms",
    "VisaNotes",
    "load_quote_presentation",
]
