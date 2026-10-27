"""Deterministic supplier simulator (task 012a1).

The simulator is a fixture adapter, not a pricing engine. It reads the synthetic
inventory and scenario shapes from ``data/synthetic`` and reproduces the events
the real adapters must survive: price drift, sold out, timeouts, partial failures
and hold expiry. Every failure is typed and immediate; it never sleeps for the
fixture's ``after_ms`` and never calls the network.

Scenarios activate at their ``at`` timestamp and stay active. A price change
overrides the net price only (a fixture carries no tax rule, so taxes are left
exactly as the price point states); a sold-out scenario forces zero availability;
a timeout makes offer reads fail with ``SupplierTimeoutError``; a partial failure
marks whole product legs as failed in search; a hold-expiry scenario sets the hold
window. Nothing here turns a supplier rule into customer-facing policy.

Not thread-safe: construct one per worker/test. Holds and bookings are kept in
memory for the process; the snapshot store and persistence arrive in task 012a2.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Final, cast

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictInt,
    ValidationError,
    model_validator,
)

from travelos.core.clock import Clock, SystemClock
from travelos.core.errors import ValidationError as CoreValidationError
from travelos.core.ids import uuid7
from travelos.core.money import Money
from travelos.suppliers.errors import (
    BookingNotFoundError,
    BookingStateError,
    HoldExpiredError,
    HoldNotFoundError,
    IdempotencyConflictError,
    OfferNotFoundError,
    OfferUnavailableError,
    SupplierSimulatorError,
    SupplierTimeoutError,
)
from travelos.suppliers.models import (
    Booking,
    BookingRequest,
    CancellationResult,
    CancellationRules,
    CancelRequest,
    CurrencyCode,
    FailureCode,
    FareRules,
    Hold,
    HoldRequest,
    Offer,
    ProductType,
    SearchFailure,
    SearchLeg,
    SearchResult,
    ShortText,
    SupplierSearchQuery,
    UtcDateTime,
    WalletBalance,
)

DATASET_DIR: Final = Path(__file__).resolve().parents[3] / "data" / "synthetic"
DEFAULT_INVENTORY_PATH: Final = DATASET_DIR / "supplier_inventory.jsonl"
DEFAULT_SCENARIOS_PATH: Final = DATASET_DIR / "supplier_scenarios.jsonl"

Paise = Annotated[StrictInt, Field(ge=0)]
ScenarioId = ShortText


class ScenarioType(StrEnum):
    """The failure/drift fixtures the simulator understands."""

    PRICE_CHANGE = "price_change"
    SOLD_OUT = "sold_out"
    TIMEOUT = "timeout"
    PARTIAL_FAILURE = "partial_failure"
    HOLD_EXPIRY = "hold_expiry"


# ---------------------------------------------------------------------------
# Fixture parsing models
# ---------------------------------------------------------------------------


class _RawFareRules(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    refundable: bool
    change_fee_paise: Paise
    cancellation_days: Annotated[StrictInt, Field(ge=0, le=365)]


class _RawPricePoint(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    at: UtcDateTime
    net_paise: Paise
    taxes_paise: Paise
    available_count: Annotated[StrictInt, Field(ge=0)]
    fare_rules: _RawFareRules


class _RawInventory(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: ShortText
    supplier: ShortText
    product_type: ProductType
    name: Annotated[str, Field(min_length=1, max_length=300)]
    route: Annotated[str | None, Field(max_length=200)] = None
    city: Annotated[str, Field(min_length=2, max_length=120)]
    valid_from: date
    valid_until: date
    currency: CurrencyCode
    price_points: Annotated[tuple[_RawPricePoint, ...], Field(min_length=1, max_length=50)]

    @model_validator(mode="after")
    def _validity_is_ordered(self) -> _RawInventory:
        if self.valid_until < self.valid_from:
            raise ValueError("valid_until must not be before valid_from")
        return self


class _PriceChangeDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    old_net_paise: Paise | None = None
    new_net_paise: Paise
    change_pct: Annotated[StrictInt, Field(ge=-100, le=1_000)] | None = None


class _SoldOutDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    available_count: Annotated[StrictInt, Field(ge=0)]

    @model_validator(mode="after")
    def _is_zero(self) -> _SoldOutDetails:
        if self.available_count != 0:
            raise ValueError("sold_out available_count must be 0")
        return self


class _TimeoutDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    after_ms: Annotated[StrictInt, Field(gt=0)]
    retryable: bool = True


class _PartialFailureDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    succeeded_legs: tuple[ProductType, ...] = ()
    failed_legs: Annotated[tuple[ProductType, ...], Field(min_length=1)]


class _HoldExpiryDetails(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    hold_seconds: Annotated[StrictInt, Field(gt=0, le=86_400)]
    expires: bool = True


ScenarioDetails = (
    _PriceChangeDetails
    | _SoldOutDetails
    | _TimeoutDetails
    | _PartialFailureDetails
    | _HoldExpiryDetails
)


class _RawScenario(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    id: ScenarioId
    sku_id: ShortText
    scenario: ScenarioType
    at: UtcDateTime
    details: dict[str, object]


_DETAIL_MODELS: Final[dict[ScenarioType, type[BaseModel]]] = {
    ScenarioType.PRICE_CHANGE: _PriceChangeDetails,
    ScenarioType.SOLD_OUT: _SoldOutDetails,
    ScenarioType.TIMEOUT: _TimeoutDetails,
    ScenarioType.PARTIAL_FAILURE: _PartialFailureDetails,
    ScenarioType.HOLD_EXPIRY: _HoldExpiryDetails,
}


# ---------------------------------------------------------------------------
# Internal state
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _PricePoint:
    at: datetime
    net_paise: int
    taxes_paise: int
    available_count: int
    refundable: bool
    change_fee_paise: int
    cancellation_days: int


@dataclass(frozen=True, slots=True)
class _Item:
    offer_id: str
    supplier: str
    product_type: ProductType
    name: str
    route: str | None
    city: str
    valid_from: date
    valid_until: date
    currency: str
    price_points: tuple[_PricePoint, ...]


@dataclass(frozen=True, slots=True)
class _Scenario:
    id: str
    offer_id: str
    kind: ScenarioType
    at: datetime
    details: ScenarioDetails


@dataclass(frozen=True, slots=True)
class _Effective:
    offer: Offer
    timed_out: bool = False
    retryable: bool = True


@dataclass(frozen=True, slots=True)
class _HoldRecord:
    fingerprint: tuple[str, int]
    hold: Hold


@dataclass(frozen=True, slots=True)
class _BookingRecord:
    hold_id: str
    booking: Booking


# ---------------------------------------------------------------------------
# Parsing helpers
# ---------------------------------------------------------------------------


def _read_jsonl(path: Path) -> list[Mapping[str, object]]:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise SupplierSimulatorError(f"cannot read {path}: {exc}") from exc

    rows: list[Mapping[str, object]] = []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise SupplierSimulatorError(f"{path}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(value, Mapping):
            raise SupplierSimulatorError(f"{path}:{number}: record must be a JSON object")
        rows.append(cast(Mapping[str, object], value))
    return rows


def _parse_details(
    kind: ScenarioType, details: Mapping[str, object], scenario_id: str
) -> ScenarioDetails:
    model = _DETAIL_MODELS[kind]
    try:
        parsed = model.model_validate(details)
    except ValidationError as exc:
        raise SupplierSimulatorError(
            f"scenario {scenario_id} ({kind.value}) details are invalid: {exc}"
        ) from exc
    return cast(ScenarioDetails, parsed)


def _parse_inventory(supplier: str, records: Iterable[Mapping[str, object]]) -> dict[str, _Item]:
    items: dict[str, _Item] = {}
    for index, record in enumerate(records, start=1):
        try:
            raw = _RawInventory.model_validate(record)
        except ValidationError as exc:
            label = record.get("id") if isinstance(record, Mapping) else None
            raise SupplierSimulatorError(
                f"invalid inventory record {label!r} (#{index}): {exc}"
            ) from exc
        if raw.supplier != supplier:
            raise SupplierSimulatorError(
                f"inventory row {raw.id} belongs to {raw.supplier}, not {supplier}"
            )
        if raw.id in items:
            raise SupplierSimulatorError(f"duplicate offer_id {raw.id} in inventory")

        items[raw.id] = _Item(
            offer_id=raw.id,
            supplier=raw.supplier,
            product_type=raw.product_type,
            name=raw.name,
            route=raw.route,
            city=raw.city,
            valid_from=raw.valid_from,
            valid_until=raw.valid_until,
            currency=raw.currency,
            price_points=tuple(
                _PricePoint(
                    at=point.at,
                    net_paise=point.net_paise,
                    taxes_paise=point.taxes_paise,
                    available_count=point.available_count,
                    refundable=point.fare_rules.refundable,
                    change_fee_paise=point.fare_rules.change_fee_paise,
                    cancellation_days=point.fare_rules.cancellation_days,
                )
                for point in raw.price_points
            ),
        )

    if not items:
        raise SupplierSimulatorError("inventory must contain at least one offer")
    return items


def _parse_scenarios(
    records: Iterable[Mapping[str, object]], known_offer_ids: set[str]
) -> tuple[_Scenario, ...]:
    scenarios: list[_Scenario] = []
    seen: set[str] = set()
    for index, record in enumerate(records, start=1):
        try:
            raw = _RawScenario.model_validate(record)
        except ValidationError as exc:
            raise SupplierSimulatorError(f"invalid scenario record (#{index}): {exc}") from exc
        if raw.id in seen:
            raise SupplierSimulatorError(f"duplicate scenario id {raw.id}")
        seen.add(raw.id)
        if raw.sku_id not in known_offer_ids:
            raise SupplierSimulatorError(f"scenario {raw.id} references unknown offer {raw.sku_id}")
        scenarios.append(
            _Scenario(
                id=raw.id,
                offer_id=raw.sku_id,
                kind=raw.scenario,
                at=raw.at,
                details=_parse_details(raw.scenario, raw.details, raw.id),
            )
        )
    return tuple(scenarios)


# ---------------------------------------------------------------------------
# Simulator
# ---------------------------------------------------------------------------


class SupplierSimulator:
    """In-memory supplier implementing :class:`SupplierAdapter`."""

    def __init__(
        self,
        *,
        supplier: str,
        inventory: Iterable[Mapping[str, object]],
        scenarios: Iterable[Mapping[str, object]] = (),
        clock: Clock | None = None,
        offer_ttl: timedelta = timedelta(minutes=10),
        hold_seconds: int = 900,
        wallet_paise: int = 0,
        wallet_currency: str = "INR",
    ) -> None:
        if not isinstance(supplier, str) or not supplier.strip():
            raise SupplierSimulatorError("supplier must be a non-empty string")
        if not isinstance(offer_ttl, timedelta) or offer_ttl <= timedelta(0):
            raise SupplierSimulatorError("offer_ttl must be a positive timedelta")
        if isinstance(hold_seconds, bool) or not isinstance(hold_seconds, int) or hold_seconds <= 0:
            raise SupplierSimulatorError("hold_seconds must be a positive integer")
        if isinstance(wallet_paise, bool) or not isinstance(wallet_paise, int) or wallet_paise < 0:
            raise SupplierSimulatorError("wallet_paise must be a non-negative integer")
        try:
            wallet = Money(wallet_paise, wallet_currency)
        except CoreValidationError as exc:
            raise SupplierSimulatorError(f"invalid wallet: {exc}") from exc

        self._supplier = supplier.strip()
        self._offer_ttl = offer_ttl
        self._hold_seconds = hold_seconds
        self._wallet_paise = wallet.paise
        self._wallet_currency = wallet.currency
        self._clock: Clock = clock or SystemClock()

        self._items = _parse_inventory(self._supplier, inventory)
        self._scenarios = _parse_scenarios(scenarios, set(self._items))

        self._holds_by_key: dict[str, _HoldRecord] = {}
        self._holds_by_id: dict[str, Hold] = {}
        self._booked_holds: dict[str, str] = {}
        self._bookings_by_key: dict[str, _BookingRecord] = {}
        self._bookings_by_ref: dict[str, Booking] = {}
        self._cancellations_by_ref: dict[str, CancellationResult] = {}
        self._cancellations_by_key: dict[str, tuple[str, CancellationResult]] = {}

    # -- construction -------------------------------------------------------

    @classmethod
    def from_dataset(
        cls,
        supplier: str,
        *,
        inventory_path: Path | None = None,
        scenarios_path: Path | None = None,
        clock: Clock | None = None,
        offer_ttl: timedelta = timedelta(minutes=10),
        hold_seconds: int = 900,
        wallet_paise: int = 0,
        wallet_currency: str = "INR",
    ) -> SupplierSimulator:
        """Load one supplier from the committed synthetic dataset.

        Rows for other suppliers and scenarios that reference them are ignored, so
        one simulator always represents exactly one supplier. A missing supplier
        is a configuration error, not an empty search.
        """

        inventory_file = inventory_path or DEFAULT_INVENTORY_PATH
        scenario_file = scenarios_path or DEFAULT_SCENARIOS_PATH
        rows = _read_jsonl(inventory_file)
        matching = [
            row
            for row in rows
            if isinstance(row.get("supplier"), str)
            and str(row["supplier"]).strip() == supplier.strip()
        ]
        if not matching:
            available = sorted(
                {
                    str(row["supplier"]).strip()
                    for row in rows
                    if isinstance(row.get("supplier"), str)
                }
            )
            raise SupplierSimulatorError(
                f"No supplier {supplier!r} in {inventory_file}; available: {', '.join(available)}"
            )

        known = {str(row.get("id")) for row in matching}
        scenario_rows = [
            row for row in _read_jsonl(scenario_file) if str(row.get("sku_id")) in known
        ]
        return cls(
            supplier=supplier,
            inventory=matching,
            scenarios=scenario_rows,
            clock=clock,
            offer_ttl=offer_ttl,
            hold_seconds=hold_seconds,
            wallet_paise=wallet_paise,
            wallet_currency=wallet_currency,
        )

    # -- adapter surface ----------------------------------------------------

    @property
    def name(self) -> str:
        return self._supplier

    def search(self, query: SupplierSearchQuery) -> SearchResult:
        now = self._clock.now()
        partial_legs = self._partial_failure_legs(now)
        offers: dict[str, Offer] = {}
        failures: dict[tuple[ProductType, FailureCode], SearchFailure] = {}

        for leg in query.legs:
            if leg.product_type in partial_legs:
                failures.setdefault(
                    (leg.product_type, FailureCode.PARTIAL_FAILURE),
                    SearchFailure(
                        product_type=leg.product_type,
                        code=FailureCode.PARTIAL_FAILURE,
                        message=f"{self._supplier} could not read the {leg.product_type} leg",
                        retryable=False,
                    ),
                )
                continue

            leg_offers: list[Offer] = []
            leg_timed_out = False
            timeout_retryable = True
            for item in self._items.values():
                if not self._matches(item, leg):
                    continue
                effective = self._effective(item, now)
                if effective is None:
                    continue
                if effective.timed_out:
                    leg_timed_out = True
                    timeout_retryable = timeout_retryable and effective.retryable
                    continue
                if effective.offer.is_available:
                    leg_offers.append(effective.offer)

            leg_offers.sort(key=lambda offer: (offer.total_paise, offer.offer_id))
            for offer in leg_offers[: query.max_results_per_leg]:
                offers.setdefault(offer.offer_id, offer)

            if leg_timed_out:
                failures.setdefault(
                    (leg.product_type, FailureCode.TIMEOUT),
                    SearchFailure(
                        product_type=leg.product_type,
                        code=FailureCode.TIMEOUT,
                        message=f"{self._supplier} timed out reading the {leg.product_type} leg",
                        retryable=timeout_retryable,
                    ),
                )

        return SearchResult(
            supplier=self._supplier,
            offers=tuple(offers.values()),
            failures=tuple(failures.values()),
            fetched_at=now,
        )

    def price(self, offer_id: str) -> Offer:
        effective = self._effective_required(offer_id, "price")
        if not effective.offer.is_available:
            raise OfferUnavailableError(self._supplier, offer_id, "sold out")
        return effective.offer

    def rules(self, offer_id: str) -> FareRules:
        return self._effective_required(offer_id, "rules").offer.fare_rules

    def hold(self, request: HoldRequest) -> Hold:
        now = self._clock.now()
        fingerprint = (request.offer_id, request.traveller_count)
        existing = self._holds_by_key.get(request.idempotency_key)
        if existing is not None:
            if existing.fingerprint != fingerprint:
                raise IdempotencyConflictError(self._supplier, request.idempotency_key)
            return existing.hold

        effective = self._effective_required(request.offer_id, "hold")
        if not effective.offer.is_available:
            raise OfferUnavailableError(self._supplier, request.offer_id, "sold out")
        if effective.offer.available_count < request.traveller_count:
            raise OfferUnavailableError(
                self._supplier,
                request.offer_id,
                (
                    f"only {effective.offer.available_count} seats for "
                    f"{request.traveller_count} travellers"
                ),
            )

        hold = Hold(
            supplier=self._supplier,
            hold_id=f"hold_{uuid7().hex}",
            offer=effective.offer,
            traveller_count=request.traveller_count,
            created_at=now,
            expires_at=self._hold_expiry(request.offer_id, now),
        )
        self._holds_by_key[request.idempotency_key] = _HoldRecord(
            fingerprint=fingerprint, hold=hold
        )
        self._holds_by_id[hold.hold_id] = hold
        return hold

    def book(self, request: BookingRequest) -> Booking:
        now = self._clock.now()
        existing = self._bookings_by_key.get(request.idempotency_key)
        if existing is not None:
            if existing.hold_id != request.hold_id:
                raise IdempotencyConflictError(self._supplier, request.idempotency_key)
            return existing.booking

        hold = self._holds_by_id.get(request.hold_id)
        if hold is None:
            raise HoldNotFoundError(self._supplier, request.hold_id)
        if request.hold_id in self._booked_holds:
            raise BookingStateError(f"{self._supplier} hold {request.hold_id} is already booked")
        expires_at = hold.expires_at
        if expires_at is not None and now >= expires_at:
            raise HoldExpiredError(self._supplier, hold.hold_id, expires_at)

        booking = Booking(
            supplier=self._supplier,
            booking_ref=f"bkg_{uuid7().hex}",
            hold_id=hold.hold_id,
            offer_id=hold.offer.offer_id,
            traveller_count=hold.traveller_count,
            currency=hold.offer.currency,
            net_paise=hold.offer.net_paise,
            taxes_paise=hold.offer.taxes_paise,
            fare_rules=hold.offer.fare_rules,
            state="confirmed",
            booked_at=now,
            cancelled_at=None,
        )
        self._booked_holds[hold.hold_id] = booking.booking_ref
        self._bookings_by_ref[booking.booking_ref] = booking
        self._bookings_by_key[request.idempotency_key] = _BookingRecord(
            hold_id=hold.hold_id, booking=booking
        )
        return booking

    def cancel(self, request: CancelRequest) -> CancellationResult:
        now = self._clock.now()
        keyed = self._cancellations_by_key.get(request.idempotency_key)
        if keyed is not None:
            if keyed[0] != request.booking_ref:
                raise IdempotencyConflictError(self._supplier, request.idempotency_key)
            return keyed[1]

        booking = self._bookings_by_ref.get(request.booking_ref)
        if booking is None:
            raise BookingNotFoundError(self._supplier, request.booking_ref)

        existing = self._cancellations_by_ref.get(request.booking_ref)
        if existing is not None:
            self._cancellations_by_key[request.idempotency_key] = (
                request.booking_ref,
                existing,
            )
            return existing

        total = booking.net_paise + booking.taxes_paise
        if booking.fare_rules.refundable:
            refund_paise, fee_paise = total, 0
        else:
            refund_paise, fee_paise = 0, total

        result = CancellationResult(
            booking_ref=booking.booking_ref,
            refund_paise=refund_paise,
            fee_paise=fee_paise,
            currency=booking.currency,
            cancelled_at=now,
        )
        self._cancellations_by_ref[booking.booking_ref] = result
        self._bookings_by_ref[booking.booking_ref] = Booking.model_validate(
            {
                **booking.model_dump(),
                "state": "cancelled",
                "cancelled_at": now,
            }
        )
        self._cancellations_by_key[request.idempotency_key] = (
            request.booking_ref,
            result,
        )
        return result

    def retrieve(self, booking_ref: str) -> Booking:
        booking = self._bookings_by_ref.get(booking_ref)
        if booking is None:
            raise BookingNotFoundError(self._supplier, booking_ref)
        return booking

    def wallet_balance(self) -> WalletBalance:
        return WalletBalance(
            supplier=self._supplier,
            balance_paise=self._wallet_paise,
            currency=self._wallet_currency,
            as_of=self._clock.now(),
        )

    # -- internals ----------------------------------------------------------

    def _require_item(self, offer_id: str) -> _Item:
        item = self._items.get(offer_id)
        if item is None:
            raise OfferNotFoundError(self._supplier, offer_id)
        return item

    def _effective_required(self, offer_id: str, operation: str) -> _Effective:
        item = self._require_item(offer_id)
        effective = self._effective(item, self._clock.now())
        if effective is None:
            raise OfferUnavailableError(self._supplier, offer_id, "not on sale yet")
        if effective.timed_out:
            raise SupplierTimeoutError(
                self._supplier, operation, offer_id, retryable=effective.retryable
            )
        return effective

    def _matches(self, item: _Item, leg: SearchLeg) -> bool:
        if item.product_type != leg.product_type:
            return False
        if leg.city is not None and item.city.casefold() != leg.city.casefold():
            return False
        if leg.route is not None and (
            item.route is None or item.route.casefold() != leg.route.casefold()
        ):
            return False
        return leg.travel_date is None or (item.valid_from <= leg.travel_date <= item.valid_until)

    def _latest_active(self, kind: ScenarioType, offer_id: str, now: datetime) -> _Scenario | None:
        selected: _Scenario | None = None
        for scenario in self._scenarios:
            if scenario.kind != kind or scenario.offer_id != offer_id or scenario.at > now:
                continue
            if selected is None or scenario.at >= selected.at:
                selected = scenario
        return selected

    def _partial_failure_legs(self, now: datetime) -> set[ProductType]:
        failed: set[ProductType] = set()
        for scenario in self._scenarios:
            if scenario.kind != ScenarioType.PARTIAL_FAILURE or scenario.at > now:
                continue
            details = scenario.details
            if not isinstance(details, _PartialFailureDetails):
                raise SupplierSimulatorError(
                    f"scenario {scenario.id} has mismatched partial-failure details"
                )
            failed.update(details.failed_legs)
        return failed

    def _hold_expiry(self, offer_id: str, now: datetime) -> datetime | None:
        scenario = self._latest_active(ScenarioType.HOLD_EXPIRY, offer_id, now)
        if scenario is None:
            return now + timedelta(seconds=self._hold_seconds)
        details = scenario.details
        if not isinstance(details, _HoldExpiryDetails):
            raise SupplierSimulatorError(
                f"scenario {scenario.id} has mismatched hold-expiry details"
            )
        if not details.expires:
            return None
        return now + timedelta(seconds=details.hold_seconds)

    def _current_price_point(self, item: _Item, now: datetime) -> _PricePoint | None:
        selected: _PricePoint | None = None
        for point in item.price_points:
            if point.at <= now and (selected is None or point.at >= selected.at):
                selected = point
        return selected

    def _effective(self, item: _Item, now: datetime) -> _Effective | None:
        point = self._current_price_point(item, now)
        if point is None:
            return None

        net_paise = point.net_paise
        change = self._latest_active(ScenarioType.PRICE_CHANGE, item.offer_id, now)
        if change is not None:
            change_details = change.details
            if not isinstance(change_details, _PriceChangeDetails):
                raise SupplierSimulatorError(
                    f"scenario {change.id} has mismatched price-change details"
                )
            net_paise = change_details.new_net_paise

        available = point.available_count
        if self._latest_active(ScenarioType.SOLD_OUT, item.offer_id, now) is not None:
            available = 0

        timed_out = False
        retryable = True
        timeout = self._latest_active(ScenarioType.TIMEOUT, item.offer_id, now)
        if timeout is not None:
            timeout_details = timeout.details
            if not isinstance(timeout_details, _TimeoutDetails):
                raise SupplierSimulatorError(
                    f"scenario {timeout.id} has mismatched timeout details"
                )
            timed_out = True
            retryable = timeout_details.retryable

        rules = FareRules(
            cancellation=CancellationRules(
                refundable=point.refundable,
                cancellation_days=point.cancellation_days,
                change_fee_paise=point.change_fee_paise,
                currency=item.currency,
            )
        )
        offer = Offer(
            supplier=item.supplier,
            offer_id=item.offer_id,
            product_type=item.product_type,
            name=item.name,
            city=item.city,
            route=item.route,
            currency=item.currency,
            net_paise=net_paise,
            taxes_paise=point.taxes_paise,
            available_count=available,
            fare_rules=rules,
            fetched_at=now,
            expires_at=now + self._offer_ttl,
        )
        return _Effective(offer=offer, timed_out=timed_out, retryable=retryable)
