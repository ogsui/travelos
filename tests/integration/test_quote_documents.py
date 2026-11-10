"""Build and render customer documents from stored quotes (task 016b2b).

The builder reads persisted quote revisions, the itinerary they cite and the
owner's presentation rules. These tests prove that the document carries the
stored total and only the stored ``tax`` lines, that staff labels and order are
preserved for a group, that an incomplete owner config is refused before any
audit row is written, and that a requested PDF is returned but never persisted.

Every price, rule, tax rate, name, label and term here is a synthetic engineering
fixture: no owner policy and no real customer data.
"""

from __future__ import annotations

import base64
import hashlib
import itertools
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import pytest
import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.core.models import AuditLog, User
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
from travelos.quotes.errors import (
    QuoteGroupNotFoundError,
    QuoteNotFoundError,
    QuotePresentationError,
)
from travelos.quotes.groups import QuoteGroupStore
from travelos.quotes.models import LineKind, QuoteGroupDraft, QuoteGroupOption
from travelos.quotes.records import QuoteGroupRecord, QuoteRecord, QuoteRevisionRecord
from travelos.quotes.rendering import QuoteRenderer
from travelos.quotes.store import QuoteStore
from travelos.suppliers.models import CancellationRules, FareRules, Offer

pytestmark = pytest.mark.integration

NOW = datetime(2026, 11, 10, 9, 0, tzinfo=UTC)
START = date(2027, 2, 10)
END = date(2027, 2, 13)
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+ip1sAAAAASUVORK5CYII="
)
_VERSIONS = itertools.count(1)


# ---------------------------------------------------------------------------
# Synthetic pricing and itinerary fixtures
# ---------------------------------------------------------------------------


def make_user(db: Session) -> User:
    user = User(
        email=f"owner-{uuid4().hex[:8]}@example.test",
        password_hash="x" * 20,
        role="owner",
    )
    db.add(user)
    db.flush()
    return user


def ruleset(*, version: int, tax_table_version: int) -> PricingRuleSet:
    return PricingRuleSet(
        version=version,
        rounding=RoundingRule(mode=RoundingMode.NEAREST, step_paise=100),
        quote_validity_hours=168,
        revalidation=RevalidationTolerance(absolute_paise=50_000, percent_bps=200),
        forex_buffer_bps=150,
        gateway_fee_policy=GatewayFeePolicy.PASSTHROUGH,
        margin_combination=MarginCombination.MAX,
        margins={
            product: MarginRule(min_paise=5_000, min_bps=300, target_bps=800, max_bps=1_500)
            for product in PricingProduct
        },
        segment_discount_budget_bps={segment: 200 for segment in CustomerSegment},
        competitor=CompetitorPolicy(
            max_age=timedelta(hours=24),
            min_comparability=80,
            undercut_paise=10_000,
        ),
        tax_table_version=tax_table_version,
        gst_basis_by_product={product: GstBasis.COMMISSION for product in PricingProduct},
        tcs=TcsPolicy(enabled=False, rules_ref=None),
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
    db: Session, *, net_paise: int = 100_000, now: datetime = NOW
) -> tuple[PriceCalculationRecord, PriceResult]:
    version = next(_VERSIONS)
    pricing = PricingStore(db)
    stored_rules = pricing.save_rule_set(
        ruleset(version=version, tax_table_version=version),
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
        items=(ItemCost(reference="hotel-1", product=PricingProduct.HOTEL, net=money(net_paise)),),
        segment=CustomerSegment.NEW,
        now=now,
    )
    result = price_quote(request)
    row = pricing.record_calculation(
        request,
        result,
        rule_set_id=stored_rules.id,
        tax_table_id=stored_tax.id,
    )
    return row, result


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
    requirement = Requirement(
        trip_type="package",
        origin_city="Delhi",
        destinations=["Goa"],
        start_date=START,
        end_date=END,
        adults=2,
        rooms=1,
    )
    build = build_package(requirement, offers, now=NOW)
    assert build.itinerary is not None
    return build.itinerary


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
    itinerary_id = ItineraryStore(db).record(sample_itinerary(), inquiry_id=inquiry_id).id
    calculation, result = record_price(db, net_paise=net_paise, now=now)
    assert result.customer_price is not None
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
    labels: list[str],
) -> QuoteGroupRecord:
    draft = QuoteGroupDraft(
        options=tuple(
            QuoteGroupOption(label=label, quote_id=quote_id)
            for label, quote_id in zip(labels, quote_ids, strict=True)
        )
    )
    return QuoteGroupStore(db).create(
        inquiry_id=inquiry_id,
        title="Synthetic trip options",
        draft=draft,
    )


# ---------------------------------------------------------------------------
# Owner presentation fixture (synthetic; not owner or legal wording)
# ---------------------------------------------------------------------------


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


def write_config(tmp_path: Path, *, mutate: Any = None) -> Path:
    data = presentation_data()
    if mutate is not None:
        mutate(data)
    assets = tmp_path / "assets"
    assets.mkdir(exist_ok=True)
    (assets / "logo.png").write_bytes(PNG)
    path = tmp_path / "business_rules.yaml"
    path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
    return path


# ---------------------------------------------------------------------------
# Document building and rendering
# ---------------------------------------------------------------------------


def test_a_single_quote_document_carries_the_stored_total_and_only_tax_lines(
    db: Session, tmp_path: Path
) -> None:
    quote = make_quote_for(db, None)
    revision = QuoteStore(db).load(quote.id).current
    expected_tax = tuple(line for line in revision.lines if line.kind is LineKind.TAX)

    rendered = QuoteRenderer(db, presentation_path=write_config(tmp_path)).render_quote(
        quote.id, now=NOW
    )

    document = rendered.document
    assert document.kind == "quote"
    assert document.title is None
    option = document.options[0]
    assert option.label is None
    assert option.total == revision.total
    assert option.tax_lines == expected_tax
    assert option.revision_number == revision.number
    assert option.travellers == 2
    assert "MockAir Delhi to Goa" in rendered.html
    assert "Mock Hotels Goa" in rendered.html
    assert "To be confirmed" in rendered.html
    assert "Synthetic terms for tests only." in rendered.html
    assert "Agency margin" not in rendered.html


def test_rendering_is_audited_without_customer_data(db: Session, tmp_path: Path) -> None:
    quote = make_quote_for(db, None)
    config = write_config(tmp_path)

    QuoteRenderer(db, presentation_path=config).render_quote(quote.id, now=NOW)

    entries = list(
        db.execute(
            select(AuditLog).where(
                AuditLog.action == "quote.rendered",
                AuditLog.subject_id == quote.id,
            )
        ).scalars()
    )
    assert len(entries) == 1
    after = entries[0].after
    assert after is not None
    assert after["kind"] == "quote"
    assert after["option_count"] == 1
    assert after["revision_numbers"] == [1]
    assert after["format"] == "html"
    assert after["presentation_hash"] == hashlib.sha256(config.read_bytes()).hexdigest()
    assert "customer" not in after
    assert "email" not in after
    assert "phone" not in after


def test_an_incomplete_owner_config_is_refused_before_any_audit_row(
    db: Session, tmp_path: Path
) -> None:
    quote = make_quote_for(db, None)

    def blank(data: dict[str, Any]) -> None:
        data["quote_presentation"]["brand"]["display_name"] = None

    renderer = QuoteRenderer(db, presentation_path=write_config(tmp_path, mutate=blank))

    with pytest.raises(QuotePresentationError, match="needs owner configuration"):
        renderer.render_quote(quote.id, now=NOW)

    entries = db.execute(select(AuditLog).where(AuditLog.action == "quote.rendered")).all()
    assert entries == []


def test_a_group_document_uses_the_staff_labels_in_order(db: Session, tmp_path: Path) -> None:
    inquiry_id = make_inquiry(db)
    first = make_quote_for(db, inquiry_id)
    second = make_quote_for(db, inquiry_id, net_paise=110_000, now=NOW + timedelta(hours=1))
    group = make_group(db, inquiry_id, [first.id, second.id], labels=["Beach", "City"])

    rendered = QuoteRenderer(db, presentation_path=write_config(tmp_path)).render_group(
        group.id, now=NOW
    )

    document = rendered.document
    assert document.kind == "group"
    assert document.title == "Synthetic trip options"
    assert [option.label for option in document.options] == ["Beach", "City"]
    assert [option.quote_id for option in document.options] == [first.id, second.id]
    assert document.expires_at == min(first.expires_at, second.expires_at)
    assert rendered.html.index("Beach") < rendered.html.index("City")


def test_an_expired_quote_is_marked_in_the_document(db: Session, tmp_path: Path) -> None:
    quote = make_quote_for(db, None)

    rendered = QuoteRenderer(db, presentation_path=write_config(tmp_path)).render_quote(
        quote.id, now=quote.expires_at + timedelta(minutes=1)
    )

    assert rendered.document.expired is True
    assert "expired" in rendered.html.lower()


def test_an_unknown_quote_or_group_is_refused(db: Session, tmp_path: Path) -> None:
    renderer = QuoteRenderer(db, presentation_path=write_config(tmp_path))

    with pytest.raises(QuoteNotFoundError):
        renderer.render_quote(uuid4(), now=NOW)
    with pytest.raises(QuoteGroupNotFoundError):
        renderer.render_group(uuid4(), now=NOW)


def test_pdf_bytes_are_returned_but_never_persisted(db: Session, tmp_path: Path) -> None:
    quote = make_quote_for(db, None)
    renderer = QuoteRenderer(
        db,
        presentation_path=write_config(tmp_path),
        pdf_renderer=lambda html: b"%PDF-1.7 synthetic",
    )

    rendered = renderer.render_quote(quote.id, now=NOW, as_pdf=True)

    assert rendered.pdf == b"%PDF-1.7 synthetic"
    rows = db.execute(
        select(QuoteRevisionRecord.pdf_ref).where(QuoteRevisionRecord.quote_id == quote.id)
    ).scalars()
    assert list(rows) == [None]


def test_gstin_is_optional_in_the_owner_config(db: Session, tmp_path: Path) -> None:
    quote = make_quote_for(db, None)

    def drop_gstin(data: dict[str, Any]) -> None:
        data["quote_presentation"]["brand"]["gstin"] = None

    rendered = QuoteRenderer(
        db, presentation_path=write_config(tmp_path, mutate=drop_gstin)
    ).render_quote(quote.id, now=NOW)

    assert rendered.document.brand.gstin is None
    assert "GSTIN" not in rendered.html


def test_a_quote_without_an_inquiry_still_renders_for_staff_preview(
    db: Session, tmp_path: Path
) -> None:
    quote = make_quote_for(db, None)
    assert quote.inquiry_id is None

    rendered = QuoteRenderer(db, presentation_path=write_config(tmp_path)).render_quote(
        quote.id, now=NOW
    )

    assert rendered.document.options[0].quote_id == quote.id
    assert isinstance(rendered.document.options[0].total, Money)
    assert isinstance(quote.expires_at, datetime)
