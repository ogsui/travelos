"""ActionPolicy catalogue and resolver: ceilings, locks and four-eyes."""

from __future__ import annotations

import pytest

from travelos.approval.errors import PolicyViolationError, UnknownActionError
from travelos.approval.matrix import (
    CATALOG,
    AutonomyLevel,
    Decision,
    effective_level,
    get_spec,
    resolve,
    validate_level,
)

LOCKED = {
    "booking.confirm",
    "booking.cancel_amend_refund",
    "campaign.audience",
    "crm.bulk_import",
    "marketing.publish_testimonial",
    "privacy.customer_delete",
    "quote.below_floor",
    "payment.supplier_payment",
    "billing.tax_filing",
    "ads.budget_increase",
    "outreach.cold_contact",
    "admin.change_autonomy",
    "admin.change_kill_switch",
}


def test_catalogue_covers_every_matrix_row_and_is_unique() -> None:
    assert len(CATALOG) >= 45
    assert len(CATALOG) == len(set(CATALOG))


def test_every_locked_action_is_frozen_and_every_default_is_within_its_ceiling() -> None:
    for action_type, spec in CATALOG.items():
        assert spec.default_level.rank <= spec.max_level.rank, action_type
        if spec.locked:
            assert spec.max_level is spec.default_level, action_type
            assert not spec.may_graduate, action_type


def test_locked_set_matches_the_approval_matrix() -> None:
    assert {name for name, spec in CATALOG.items() if spec.locked} == LOCKED


def test_owner_only_actions_are_the_locked_financial_and_admin_ones() -> None:
    assert {name for name, spec in CATALOG.items() if spec.owner_only} == {
        "admin.change_autonomy",
        "admin.change_kill_switch",
        "ads.budget_increase",
        "billing.tax_filing",
        "outreach.cold_contact",
        "payment.supplier_payment",
        "privacy.customer_delete",
        "quote.below_floor",
    }


@pytest.mark.parametrize(
    ("action_type", "default", "maximum"),
    [
        ("crm.capture_lead", "A3", "A3"),
        ("crm.bulk_import", "A1", "A1"),
        ("inquiry.parse", "A3", "A3"),
        ("quote.send", "A1", "A2"),
        ("quote.follow_up", "A1", "A2"),
        ("billing.ledger_update", "A2", "A3"),
        ("documents.send", "A2", "A2"),
        ("booking.confirm", "A1", "A1"),
        ("payment.supplier_payment", "A0", "A0"),
    ],
)
def test_catalogue_values_match_the_document(action_type: str, default: str, maximum: str) -> None:
    spec = get_spec(action_type)
    assert spec.default_level.value == default
    assert spec.max_level.value == maximum


def test_unknown_actions_fail_closed() -> None:
    with pytest.raises(UnknownActionError):
        get_spec("nobody.knows")
    with pytest.raises(UnknownActionError):
        resolve("nobody.knows")


def test_a0_denies_and_a1_needs_approval_and_a2_a3_are_auto() -> None:
    denied = resolve("payment.supplier_payment")
    assert denied.decision is Decision.DENY
    assert denied.level is AutonomyLevel.A0

    approval = resolve("quote.send")
    assert approval.decision is Decision.NEEDS_APPROVAL
    assert approval.level is AutonomyLevel.A1
    assert approval.requires_four_eyes is False

    import_decision = resolve("crm.bulk_import")
    assert import_decision.decision is Decision.NEEDS_APPROVAL
    assert import_decision.level is AutonomyLevel.A1
    assert import_decision.locked is True

    review = resolve("billing.ledger_update")
    assert review.decision is Decision.AUTO
    assert review.review_after is True

    auto = resolve("crm.capture_lead")
    assert auto.decision is Decision.AUTO
    assert auto.review_after is False


def test_locked_action_cannot_be_raised_even_if_the_stored_row_says_a3() -> None:
    decision = resolve("quote.below_floor", level="A3")
    assert decision.decision is Decision.DENY
    assert decision.level is AutonomyLevel.A0
    assert decision.configured_level is AutonomyLevel.A3
    assert "clamped" in decision.reason

    booking = resolve("booking.confirm", level="A3")
    assert booking.decision is Decision.NEEDS_APPROVAL
    assert booking.level is AutonomyLevel.A1
    assert booking.locked is True


def test_non_locked_levels_are_clamped_to_the_catalogue_ceiling() -> None:
    decision = resolve("quote.send", level="A3")
    assert decision.level is AutonomyLevel.A2
    assert decision.decision is Decision.AUTO
    assert decision.max_level is AutonomyLevel.A2
    assert "clamped" in decision.reason


def test_an_owner_may_lower_autonomy_below_the_default() -> None:
    decision = resolve("crm.capture_lead", level="A1")
    assert decision.decision is Decision.NEEDS_APPROVAL
    assert decision.level is AutonomyLevel.A1


def test_policy_four_eyes_flag_turns_auto_into_approval() -> None:
    decision = resolve("crm.capture_lead", policy_requires_four_eyes=True)
    assert decision.decision is Decision.NEEDS_APPROVAL
    assert decision.requires_four_eyes is True


def test_value_threshold_triggers_four_eyes_at_or_above_the_threshold() -> None:
    below = resolve("quote.send", value_paise=499_999, threshold_paise=500_000)
    at = resolve("quote.send", value_paise=500_000, threshold_paise=500_000)
    assert below.requires_four_eyes is False
    assert at.requires_four_eyes is True


def test_zero_threshold_never_triggers_four_eyes() -> None:
    assert resolve("quote.send", value_paise=10**9, threshold_paise=0).requires_four_eyes is False


def test_validate_level_rejects_unlocking_and_crossing_the_ceiling() -> None:
    with pytest.raises(PolicyViolationError, match="LOCKED"):
        validate_level(get_spec("booking.confirm"), AutonomyLevel.A3)
    with pytest.raises(PolicyViolationError, match="above maximum"):
        validate_level(get_spec("quote.send"), AutonomyLevel.A3)

    validate_level(get_spec("quote.send"), AutonomyLevel.A2)
    validate_level(get_spec("quote.send"), AutonomyLevel.A0)


def test_effective_level_reports_whether_it_clamped() -> None:
    assert effective_level(get_spec("quote.send"), AutonomyLevel.A1) == (
        AutonomyLevel.A1,
        False,
    )
    assert effective_level(get_spec("booking.confirm"), AutonomyLevel.A3) == (
        AutonomyLevel.A1,
        True,
    )


def test_one_below_stops_at_a0() -> None:
    assert AutonomyLevel.A3.one_below() is AutonomyLevel.A2
    assert AutonomyLevel.A1.one_below() is AutonomyLevel.A0
    assert AutonomyLevel.A0.one_below() is AutonomyLevel.A0


def test_negative_money_values_are_rejected() -> None:
    with pytest.raises(ValueError, match="negative"):
        resolve("quote.send", value_paise=-1)
