"""Normalized supplier domain models (task 012a1).

The models are the contract every supplier adapter returns, so these tests pin
the money representation (integer paise, never float), the UTC timestamp rule and
the invariants a snapshot or booking relies on.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from pydantic import ValidationError

from travelos.core.money import Money
from travelos.suppliers.models import (
    Booking,
    BookingRequest,
    CancellationResult,
    CancellationRules,
    CancelRequest,
    FailureCode,
    FareRules,
    Hold,
    HoldRequest,
    Offer,
    SearchFailure,
    SearchLeg,
    SearchResult,
    SupplierSearchQuery,
    WalletBalance,
)

NOW = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
LATER = NOW + timedelta(minutes=10)
TRAVEL_DATE = date(2026, 12, 1)


def fare_rules(*, refundable: bool = True, currency: str = "INR") -> FareRules:
    return FareRules(
        cancellation=CancellationRules(
            refundable=refundable,
            cancellation_days=7,
            change_fee_paise=250_000,
            currency=currency,
        )
    )


def offer(**overrides: object) -> Offer:
    values: dict[str, object] = {
        "supplier": "MockAir",
        "offer_id": "sku_0001",
        "product_type": "flight",
        "name": "MockAir Mumbai to Goa",
        "city": "Goa",
        "route": "Mumbai-Goa",
        "currency": "INR",
        "net_paise": 3_000_000,
        "taxes_paise": 150_000,
        "available_count": 8,
        "fare_rules": fare_rules(),
        "fetched_at": NOW,
        "expires_at": LATER,
    }
    values.update(overrides)
    return Offer.model_validate(values)


def hold(**overrides: object) -> Hold:
    values: dict[str, object] = {
        "supplier": "MockAir",
        "hold_id": "hold_0001",
        "offer": offer(),
        "traveller_count": 2,
        "created_at": NOW,
        "expires_at": NOW + timedelta(minutes=15),
    }
    values.update(overrides)
    return Hold.model_validate(values)


def booking(**overrides: object) -> Booking:
    values: dict[str, object] = {
        "supplier": "MockAir",
        "booking_ref": "bkg_0001",
        "hold_id": "hold_0001",
        "offer_id": "sku_0001",
        "traveller_count": 2,
        "currency": "INR",
        "net_paise": 3_000_000,
        "taxes_paise": 150_000,
        "fare_rules": fare_rules(),
        "state": "confirmed",
        "booked_at": NOW,
        "cancelled_at": None,
    }
    values.update(overrides)
    return Booking.model_validate(values)


# ---------------------------------------------------------------------------
# Money and fare rules
# ---------------------------------------------------------------------------


def test_fare_rules_expose_exact_money_views() -> None:
    rules = fare_rules()

    assert rules.refundable is True
    assert rules.change_fee == Money(250_000)
    assert rules.cancellation.change_fee == Money(250_000)
    assert rules.to_dict() == {
        "refundable": True,
        "cancellation_days": 7,
        "change_fee_paise": 250_000,
        "currency": "INR",
    }


@pytest.mark.parametrize("bad", [-1, True, 1.0, "250000"])
def test_fare_rules_reject_non_integer_or_negative_paise(bad: object) -> None:
    with pytest.raises(ValidationError):
        CancellationRules(
            refundable=False,
            cancellation_days=7,
            change_fee_paise=bad,  # type: ignore[arg-type]
        )


@pytest.mark.parametrize("bad", [-1, 366, True, 1.0])
def test_cancellation_days_must_be_a_bounded_integer(bad: object) -> None:
    with pytest.raises(ValidationError):
        CancellationRules(
            refundable=False,
            cancellation_days=bad,  # type: ignore[arg-type]
            change_fee_paise=0,
        )


@pytest.mark.parametrize("currency", ["inr", "IN", "INRR", ""])
def test_currency_must_be_an_uppercase_iso_code(currency: str) -> None:
    with pytest.raises(ValidationError):
        CancellationRules(
            refundable=True,
            cancellation_days=7,
            change_fee_paise=0,
            currency=currency,
        )


# ---------------------------------------------------------------------------
# Offer
# ---------------------------------------------------------------------------


def test_offer_totals_are_exact_money() -> None:
    value = offer()

    assert value.net == Money(3_000_000)
    assert value.taxes == Money(150_000)
    assert value.total == Money(3_150_000)
    assert value.total_paise == 3_150_000
    assert value.is_available is True


@pytest.mark.parametrize("bad", [-1, True, 1.0, "3000000"])
def test_offer_rejects_non_integer_or_negative_money(bad: object) -> None:
    with pytest.raises(ValidationError):
        offer(net_paise=bad)


def test_offer_requires_aware_utc_timestamps() -> None:
    with pytest.raises(ValidationError):
        offer(fetched_at=datetime(2026, 11, 1, 10, 0))

    local = datetime(2026, 11, 1, 15, 30, tzinfo=timezone(timedelta(hours=5, minutes=30)))
    normalised = offer(fetched_at=local, expires_at=local + timedelta(minutes=10))
    assert normalised.fetched_at == NOW
    assert normalised.fetched_at.utcoffset() == timedelta(0)


def test_offer_expiry_must_follow_fetch() -> None:
    with pytest.raises(ValidationError):
        offer(expires_at=NOW)
    with pytest.raises(ValidationError):
        offer(expires_at=NOW - timedelta(seconds=1))


def test_offer_reports_expiry_without_changing() -> None:
    value = offer()

    assert value.is_expired(NOW) is False
    assert value.is_expired(LATER) is True
    with pytest.raises(ValueError, match="timezone-aware"):
        value.is_expired(datetime(2026, 11, 1, 10, 0))


def test_offer_currency_must_match_fare_rules() -> None:
    with pytest.raises(ValidationError, match="currency"):
        offer(fare_rules=fare_rules(currency="USD"))


def test_zero_availability_means_not_available() -> None:
    assert offer(available_count=0).is_available is False


def test_offer_rejects_unknown_product_types() -> None:
    with pytest.raises(ValidationError):
        offer(product_type="cruise")


def test_offer_is_frozen_and_forbids_extra_fields() -> None:
    value = offer()
    with pytest.raises(ValidationError):
        value.net_paise = 1  # type: ignore[misc]
    with pytest.raises(ValidationError):
        offer(passenger_count=2)


# ---------------------------------------------------------------------------
# Search query and result
# ---------------------------------------------------------------------------


def test_a_search_leg_needs_a_city_or_route() -> None:
    with pytest.raises(ValidationError, match="city or route"):
        SearchLeg(product_type="flight")
    with pytest.raises(ValidationError, match="city or route"):
        SearchLeg(product_type="flight", city="   ", route="")


def test_search_query_requires_legs_and_unique_legs() -> None:
    leg = SearchLeg(product_type="hotel", city="Goa", travel_date=TRAVEL_DATE)

    with pytest.raises(ValidationError):
        SupplierSearchQuery(legs=())
    with pytest.raises(ValidationError, match="duplicate"):
        SupplierSearchQuery(legs=(leg, leg))


def test_search_query_bounds_results_per_leg() -> None:
    leg = SearchLeg(product_type="hotel", city="Goa")
    assert SupplierSearchQuery(legs=(leg,)).max_results_per_leg == 10
    with pytest.raises(ValidationError):
        SupplierSearchQuery(legs=(leg,), max_results_per_leg=0)
    with pytest.raises(ValidationError):
        SupplierSearchQuery(legs=(leg,), max_results_per_leg=51)


def test_search_result_flags_partial_results() -> None:
    failure = SearchFailure(
        product_type="hotel",
        code=FailureCode.PARTIAL_FAILURE,
        message="hotel leg failed",
        retryable=False,
    )
    result = SearchResult(
        supplier="MockAir",
        offers=(offer(),),
        failures=(failure,),
        fetched_at=NOW,
    )

    assert result.complete is False
    assert result.failures[0].code == "partial_failure"


def test_search_result_rejects_offers_from_another_supplier() -> None:
    with pytest.raises(ValidationError, match="supplier"):
        SearchResult(
            supplier="MockHotels",
            offers=(offer(),),
            fetched_at=NOW,
        )


def test_search_failure_needs_a_known_code_and_message() -> None:
    with pytest.raises(ValidationError):
        SearchFailure(
            product_type="hotel",
            code="exploded",  # type: ignore[arg-type]
            message="boom",
            retryable=False,
        )
    with pytest.raises(ValidationError):
        SearchFailure(
            product_type="hotel",
            code=FailureCode.UNAVAILABLE,
            message="  ",
            retryable=False,
        )


# ---------------------------------------------------------------------------
# Hold, booking and cancellation
# ---------------------------------------------------------------------------


def test_hold_expiry_must_follow_creation_or_be_absent() -> None:
    assert hold(expires_at=None).is_expired(NOW) is False
    with pytest.raises(ValidationError):
        hold(expires_at=NOW)
    with pytest.raises(ValidationError):
        hold(expires_at=datetime(2026, 11, 1, 11, 0))


def test_hold_reports_expiry_at_the_boundary() -> None:
    value = hold(expires_at=NOW + timedelta(seconds=30))

    assert value.is_expired(NOW) is False
    assert value.is_expired(NOW + timedelta(seconds=29)) is False
    assert value.is_expired(NOW + timedelta(seconds=30)) is True


def test_hold_offer_must_belong_to_the_supplier() -> None:
    with pytest.raises(ValidationError, match="supplier"):
        hold(supplier="MockHotels")


@pytest.mark.parametrize("travellers", [0, 31, True, 1.0])
def test_hold_request_bounds_travellers(travellers: object) -> None:
    with pytest.raises(ValidationError):
        HoldRequest(
            offer_id="sku_0001",
            traveller_count=travellers,  # type: ignore[arg-type]
            idempotency_key="hold-key-0001",
        )


def test_booking_state_matches_cancelled_at() -> None:
    assert booking().cancelled_at is None
    assert booking(state="cancelled", cancelled_at=NOW).state == "cancelled"
    with pytest.raises(ValidationError, match="cancelled_at"):
        booking(state="cancelled")
    with pytest.raises(ValidationError, match="cancelled_at"):
        booking(cancelled_at=NOW)
    with pytest.raises(ValidationError, match="cancelled_at"):
        booking(state="cancelled", cancelled_at=NOW - timedelta(minutes=1))


def test_booking_money_and_total() -> None:
    value = booking()

    assert value.net == Money(3_000_000)
    assert value.taxes == Money(150_000)
    assert value.total == Money(3_150_000)
    assert value.is_active is True


def test_cancellation_result_is_exact_money() -> None:
    result = CancellationResult(
        booking_ref="bkg_0001",
        refund_paise=3_150_000,
        fee_paise=0,
        currency="INR",
        cancelled_at=NOW,
    )

    assert result.state == "cancelled"
    assert result.refund == Money(3_150_000)
    assert result.fee == Money(0)


@pytest.mark.parametrize("amount", [-1, True, 1.0])
def test_cancellation_result_rejects_bad_amounts(amount: object) -> None:
    with pytest.raises(ValidationError):
        CancellationResult(
            booking_ref="bkg_0001",
            refund_paise=amount,  # type: ignore[arg-type]
            fee_paise=0,
            cancelled_at=NOW,
        )


@pytest.mark.parametrize(
    "request_model",
    [HoldRequest, BookingRequest, CancelRequest],
)
def test_every_mutation_request_needs_a_real_idempotency_key(
    request_model: type[HoldRequest | BookingRequest | CancelRequest],
) -> None:
    values: dict[str, object] = {
        "HoldRequest": {"offer_id": "sku_0001"},
        "BookingRequest": {"hold_id": "hold_0001"},
        "CancelRequest": {"booking_ref": "bkg_0001"},
    }[request_model.__name__]

    with pytest.raises(ValidationError):
        request_model.model_validate({**values, "idempotency_key": "short"})
    with pytest.raises(ValidationError):
        request_model.model_validate({**values, "idempotency_key": "x" * 201})


# ---------------------------------------------------------------------------
# Wallet
# ---------------------------------------------------------------------------


def test_wallet_balance_exposes_money_and_time() -> None:
    balance = WalletBalance(
        supplier="MockAir",
        balance_paise=5_000_000,
        currency="INR",
        as_of=NOW,
    )

    assert balance.balance == Money(5_000_000)
    with pytest.raises(ValidationError):
        WalletBalance(
            supplier="MockAir",
            balance_paise=-1,
            currency="INR",
            as_of=NOW,
        )


def test_wallet_balance_requires_aware_time() -> None:
    with pytest.raises(ValidationError):
        WalletBalance(
            supplier="MockAir",
            balance_paise=0,
            currency="INR",
            as_of=datetime(2026, 11, 1, 10, 0),
        )


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------


def test_models_round_trip_through_json_ready_data() -> None:
    """Task 012a2 will persist a snapshot from this JSON shape; it must reload."""

    offer_value = offer()
    hold_value = hold()
    booking_value = booking()
    balance = WalletBalance(
        supplier="MockAir",
        balance_paise=5_000_000,
        currency="INR",
        as_of=NOW,
    )

    payload = offer_value.model_dump(mode="json")
    assert payload["net_paise"] == 3_000_000
    assert isinstance(payload["net_paise"], int)
    assert payload["fetched_at"] == "2026-11-01T10:00:00Z"
    assert payload["fare_rules"]["cancellation"]["change_fee_paise"] == 250_000

    assert Offer.model_validate_json(offer_value.model_dump_json()) == offer_value
    assert Hold.model_validate_json(hold_value.model_dump_json()) == hold_value
    assert Booking.model_validate_json(booking_value.model_dump_json()) == booking_value
    assert WalletBalance.model_validate_json(balance.model_dump_json()) == balance
