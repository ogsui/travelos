"""Deterministic sales claim guard (task 011a)."""

from __future__ import annotations

import pytest

from travelos.conversation.guard import (
    ClaimKind,
    UnsupportedClaimError,
    require_supported,
    scan_reply,
)

CLEAN = (
    "Namaste! Thank you for contacting us. We have received your enquiry and a "
    "travel consultant will review it and reply shortly."
)


def test_a_template_without_claims_passes() -> None:
    report = scan_reply(CLEAN)
    assert report.claims == ()
    assert report.passed is True
    assert report.kinds() == ()


@pytest.mark.parametrize(
    ("body", "kind"),
    [
        ("The fare is Rs 45,000 for two adults.", ClaimKind.MONEY),
        ("Total amount: \u20b912,500.", ClaimKind.MONEY),
        ("Great news, seats are available on that flight.", ClaimKind.AVAILABILITY),
        ("The booking is confirmed and guaranteed.", ClaimKind.AVAILABILITY),
        ("Your ticket is fully refundable.", ClaimKind.POLICY),
        ("Visa processing takes ten days.", ClaimKind.POLICY),
        ("We can offer a 10% discount.", ClaimKind.DISCOUNT),
        ("We guarantee the best price.", ClaimKind.PROMISE),
        ("We will reply within 24 hours.", ClaimKind.PROMISE),
    ],
)
def test_each_claim_kind_is_detected_and_blocks_without_a_source(
    body: str, kind: ClaimKind
) -> None:
    report = scan_reply(body)
    assert kind in {claim.kind for claim in report.claims}
    assert report.passed is False
    assert kind.value in report.unsupported_kinds()


def test_a_source_of_the_right_kind_allows_the_claim() -> None:
    body = "The fare is Rs 45,000 for two adults."
    report = scan_reply(body, {"money": ["quote:rev_01"]})
    assert report.passed is True
    assert report.sources["money"] == ("quote:rev_01",)
    assert [claim.text for claim in report.claims] == ["fare", "Rs 45,000"]


def test_a_source_for_another_kind_does_not_allow_the_claim() -> None:
    body = "The fare is Rs 45,000 for two adults."
    report = scan_reply(body, {"availability": ["inventory:snap_01"]})
    assert report.passed is False
    assert report.unsupported_kinds() == ("money",)


def test_claim_spans_match_the_body() -> None:
    body = "Your ticket is fully refundable."
    claim = next(item for item in scan_reply(body).claims if item.kind is ClaimKind.POLICY)
    assert body[claim.start : claim.end] == claim.text


def test_require_supported_raises_before_anything_is_written() -> None:
    with pytest.raises(UnsupportedClaimError) as caught:
        require_supported("The fare is Rs 45,000.")
    assert caught.value.report.unsupported_kinds() == ("money",)
    assert "money" in str(caught.value)


def test_require_supported_returns_a_passed_report() -> None:
    report = require_supported(CLEAN)
    assert report.passed is True


def test_a_quotation_mention_is_not_a_price_claim() -> None:
    """Saying we will prepare a quotation is a service step, not a price."""

    report = scan_reply("We will prepare a quotation after we speak.")
    assert report.passed is True


def test_empty_body_and_bad_sources_are_rejected() -> None:
    with pytest.raises(ValueError, match="empty reply"):
        scan_reply("   ")
    with pytest.raises(ValueError, match="unknown claim source kind"):
        scan_reply("Hello", {"prices": ["quote:1"]})
    with pytest.raises(ValueError, match="at least one non-empty ref"):
        scan_reply("Hello", {"money": []})
    with pytest.raises(ValueError, match="at least one non-empty ref"):
        scan_reply("Hello", {"money": ["  "]})


def test_money_sources_do_not_support_policy_claims() -> None:
    body = "The Rs 45,000 fare is refundable."
    report = scan_reply(body, {"money": ["quote:rev_01"]})
    assert report.passed is False
    assert report.unsupported_kinds() == ("policy",)


@pytest.mark.parametrize(
    "body",
    [
        "Aapke liye total 45,000 rupaye hoga.",
        "The package is 1.5 lakh per person.",
        "Only 2 crore for the whole group.",
        "Just 45k for two people.",
        "Budget 45,000 rupees, final.",
    ],
)
def test_hinglish_indian_and_shorthand_money_forms_are_claims(body: str) -> None:
    report = scan_reply(body)
    assert ClaimKind.MONEY in {claim.kind for claim in report.claims}
    assert report.passed is False
