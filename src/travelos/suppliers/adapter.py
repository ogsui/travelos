"""The supplier adapter port (task 012a1).

Price and inventory collection goes through owner-authorised supplier portals,
not invented APIs; the simulator is the only implementation in this slice. Every
real adapter must pass the same contract suite (``tests/contract``).

Two boundaries matter:

* The adapter only reads prices, rules, holds, bookings and the wallet. It never
  moves supplier money and it has no approval logic.
* ``book`` and ``cancel`` exist for the booking orchestrator (task 020), which is
  the only caller allowed to reach them after the approval engine authorises the
  action. They are not exposed by any API route in this slice.

No LLM call belongs behind this interface: prices, taxes and refund amounts are
deterministic supplier or system data.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from travelos.suppliers.models import (
    Booking,
    BookingRequest,
    CancellationResult,
    CancelRequest,
    FareRules,
    Hold,
    HoldRequest,
    Offer,
    SearchResult,
    SupplierSearchQuery,
    WalletBalance,
)


@runtime_checkable
class SupplierAdapter(Protocol):
    """Everything TravelOS may ask a supplier, normalized.

    Implementations must be safe to call from a worker with a per-call timeout,
    must map provider failures to ``travelos.suppliers.errors`` and must never log
    portal credentials, cookies or session tokens.
    """

    @property
    def name(self) -> str:
        """Stable supplier name used in snapshots and bookings."""
        ...

    def search(self, query: SupplierSearchQuery) -> SearchResult:
        """Return current offers, plus a failure per leg that could not be read."""
        ...

    def price(self, offer_id: str) -> Offer:
        """Revalidate one offer at its current price."""
        ...

    def rules(self, offer_id: str) -> FareRules:
        """Read the supplier's current fare rules for one offer."""
        ...

    def hold(self, request: HoldRequest) -> Hold:
        """Hold seats against one offer. Idempotent by ``request.idempotency_key``."""
        ...

    def book(self, request: BookingRequest) -> Booking:
        """Book a live hold. Orchestrator- and approval-gated; never moves money."""
        ...

    def cancel(self, request: CancelRequest) -> CancellationResult:
        """Cancel a booking. Orchestrator- and approval-gated; never moves money."""
        ...

    def retrieve(self, booking_ref: str) -> Booking:
        """Read the supplier's current view of a booking."""
        ...

    def wallet_balance(self) -> WalletBalance:
        """Read the supplier balance/credit. A read only."""
        ...
