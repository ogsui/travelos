"""Synthetic builders shared by the quote tests (tasks 016a-016c).

Everything here is an engineering fixture: margins, tax rates, offers, names and
identities are obviously fake. No owner policy, real supplier data or customer
data belongs in this module. Nothing here asserts; it only builds rows inside the
test's rolled-back transaction.
"""

from __future__ import annotations

import base64
import itertools
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import yaml
from sqlalchemy.orm import Session

from travelos.core.models import User
from travelos.core.money import Money, money
from travelos.inquiry.models import Inquiry
from travelos.inquiry.requirement import Requirement
from travelos.packages.builder import build_package
from travelos.packages.models import Itinerary
from travelos.packages.store import ItineraryStore
from travelos.pricing.calculator import ItemCost, PriceRequest, PriceResult, price_quote
from travelos.pricing.models import (
    CompetitorPolicy,
    CustomerSegment,
    GatewayFeePolicy,
    GstBasis,
    MarginCombination,
    MarginRule,
    PricingProduct,
    PricingRuleSet,
    RevalidationTolerance,
    RoundingMode,
    RoundingRule,
    TcsPolicy,
)
from travelos.pricing.records import PriceCalculationRecord
from travelos.pricing.store import PricingStore
from travelos.pricing.tax import TaxRateRow, TaxTable
from travelos.quotes.groups import QuoteGroupStore
from travelos.quotes.models import QuoteGroupDraft, QuoteGroupOption, QuoteState
from travelos.quotes.records import QuoteGroupRecord, QuoteRecord
from travelos.quotes.store import QuoteStore
from travelos.suppliers.models import CancellationRules, FareRules, Offer

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
START = date(2027, 2, 10)
END = date(2027, 2, 13)
_VERSIONS = itertools.count(1)


def make_user(db: Session) -> User:
    """Create a synthetic owner with the attributes the pricing store needs."""

    user = User(
        email=f"owner-{uuid4().hex[:8]}@example.test",
        password_hash="x" * 20,
        role="owner",
    )
    db.add(user)
    db.flush()
    return user


def ruleset(*, version: int, tax_table_version: int, tcs_enabled: bool = False) -> PricingRuleSet:
    margins = {
        product: MarginRule(min_paise=5_000, min_bps=300, target_bps=800, max_bps=1_500)
        for product in PricingProduct
    }
    return PricingRuleSet(
        version=version,
        rounding=RoundingRule(mode=RoundingMode.NEAREST, step_paise=100),
        quote_validity_hours=168,
        revalidation=RevalidationTolerance(absolute_paise=50_000, percent_bps=200),
        forex_buffer_bps=150,
        gateway_fee_policy=GatewayFeePolicy.PASSTHROUGH,
        margin_combination=MarginCombination.MAX,
        margins=margins,
        segment_discount_budget_bps={segment: 200 for segment in CustomerSegment},
        competitor=CompetitorPolicy(
            max_age=timedelta(hours=24),
            min_comparability=80,
            undercut_paise=10_000,
        ),
        tax_table_version=tax_table_version,
        gst_basis_by_product={product: GstBasis.COMMISSION for product in PricingProduct},
        tcs=TcsPolicy(
            enabled=tcs_enabled,
            rules_ref="synthetic-tcs-reference" if tcs_enabled else None,
        ),
    )


def tax_table(*, version: int) -> TaxTable:
    return TaxTable(
        version=version,
        approved_by_accountant=True,
        approved_on=date(2026, 11, 1),
        rows=tuple(
            TaxRateRow(
                product=product,
                basis=GstBasis.COMMISSION,
                rate_bps=500,
                reference=f"synthetic-{product.value}",
            )
            for product in PricingProduct
        ),
    )


def record_price(
    db: Session,
    *,
    net_paise: int = 100_000,
    now: datetime = NOW,
    proposed: Money | None = None,
    competitor: Money | None = None,
    tcs_enabled: bool = False,
    quote_revision_id: UUID | None = None,
) -> tuple[PriceCalculationRecord, PriceResult]:
    version = next(_VERSIONS)
    pricing = PricingStore(db)
    stored_rules = pricing.save_rule_set(
        ruleset(version=version, tax_table_version=version, tcs_enabled=tcs_enabled),
        approved_by=make_user(db).id,
        approved_at=now,
        active_from=now - timedelta(hours=1),
    )
    stored_tax = pricing.save_tax_table(
        tax_table(version=version),
        approved_by=make_user(db).id,
        active_from=now - timedelta(hours=1),
    )
    request = PriceRequest(
        rules=stored_rules.rules,
        tax_table=stored_tax.table,
        items=(
            ItemCost(
                reference="hotel-1",
                product=PricingProduct.HOTEL,
                net=money(net_paise),
            ),
        ),
        segment=CustomerSegment.NEW,
        competitor_reference=competitor,
        proposed_price=proposed,
        now=now,
    )
    result = price_quote(request)
    row = pricing.record_calculation(
        request,
        result,
        rule_set_id=stored_rules.id,
        tax_table_id=stored_tax.id,
        quote_revision_id=quote_revision_id,
    )
    return row, result


# ---------------------------------------------------------------------------
# A real itinerary for the quote to reference
# ---------------------------------------------------------------------------


def sample_requirement() -> Requirement:
    return Requirement(
        trip_type="package",
        origin_city="Delhi",
        destinations=["Goa"],
        start_date=START,
        end_date=END,
        adults=2,
        rooms=1,
    )


def sample_offer(
    *,
    supplier: str = "MockAir",
    offer_id: str = "air-out",
    product_type: str = "flight",
    name: str = "MockAir Delhi to Goa",
    city: str = "Goa",
    route: str | None = "Delhi-Goa",
    net_paise: int = 1_000_000,
) -> Offer:
    return Offer(
        supplier=supplier,
        offer_id=offer_id,
        product_type=product_type,
        name=name,
        city=city,
        route=route,
        net_paise=net_paise,
        taxes_paise=0,
        available_count=9,
        fare_rules=FareRules(
            cancellation=CancellationRules(
                refundable=True,
                cancellation_days=7,
                change_fee_paise=0,
            )
        ),
        fetched_at=NOW,
        expires_at=NOW + timedelta(minutes=10),
    )


def sample_itinerary() -> Itinerary:
    offers = [
        sample_offer(),
        sample_offer(offer_id="air-back", name="MockAir Goa to Delhi", route="Goa-Delhi"),
        sample_offer(
            supplier="Mock Hotels",
            offer_id="hotel-goa",
            product_type="hotel",
            name="Mock Hotels Goa",
            route=None,
            net_paise=500_000,
        ),
        sample_offer(
            supplier="Mock Transfers",
            offer_id="transfer-goa",
            product_type="transfer",
            name="Mock Transfers Goa",
            route=None,
            net_paise=100_000,
        ),
    ]
    build = build_package(sample_requirement(), offers, now=NOW)
    assert build.itinerary is not None
    return build.itinerary


def make_itinerary(db: Session, inquiry_id: UUID | None = None) -> UUID:
    return ItineraryStore(db).record(sample_itinerary(), inquiry_id=inquiry_id).id


# ---------------------------------------------------------------------------
# Quotes and groups
# ---------------------------------------------------------------------------


def make_inquiry(db: Session) -> UUID:
    inquiry = Inquiry(trip_type="package")
    db.add(inquiry)
    db.flush()
    return inquiry.id


def make_quote_for(
    db: Session,
    inquiry_id: UUID | None,
    *,
    net_paise: int = 100_000,
    now: datetime = NOW,
) -> QuoteRecord:
    itinerary_id = make_itinerary(db, inquiry_id=inquiry_id)
    calculation, _ = record_price(db, net_paise=net_paise, now=now)
    return QuoteStore(db).create(
        calculation_id=calculation.id,
        itinerary_id=itinerary_id,
        inquiry_id=inquiry_id,
    )


def make_group(
    db: Session,
    inquiry_id: UUID,
    quote_ids: list[UUID],
    *,
    labels: list[str] | None = None,
) -> QuoteGroupRecord:
    names = labels or [f"Option {chr(ord('A') + index)}" for index in range(len(quote_ids))]
    draft = QuoteGroupDraft(
        options=tuple(
            QuoteGroupOption(label=label, quote_id=quote_id)
            for label, quote_id in zip(names, quote_ids, strict=True)
        )
    )
    return QuoteGroupStore(db).create(
        inquiry_id=inquiry_id,
        title="Synthetic trip options",
        draft=draft,
    )


def move_to(db: Session, quote: QuoteRecord, state: QuoteState) -> QuoteRecord:
    """Set the lifecycle state directly, as an earlier step would have."""

    quote.state = state.value
    db.flush()
    return quote


# ---------------------------------------------------------------------------
# A synthetic owner presentation config (never real brand or legal wording)
# ---------------------------------------------------------------------------

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)


def presentation_data() -> dict[str, Any]:
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
        }
    }


def write_presentation_config(tmp_path: Path, *, mutate: Any = None) -> Path:
    """Write a complete synthetic ``quote_presentation`` section and its logo."""

    data = presentation_data()
    if mutate is not None:
        mutate(data)
    assets = tmp_path / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "logo.png").write_bytes(PNG)
    path = tmp_path / "business_rules.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path
