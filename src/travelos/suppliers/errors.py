"""Expected supplier-domain errors.

Adapters translate transport, portal and provider responses into these types so a
caller (search orchestrator, booking orchestrator) can decide between "retry",
"show the user no availability" and "stop and ask a human". Messages never carry
credentials, cookies or session tokens.
"""

from __future__ import annotations

from datetime import datetime

from travelos.core.errors import TravelOSError


class SupplierError(TravelOSError):
    """Base class for expected supplier failures."""


class SupplierValidationError(SupplierError):
    """The caller supplied an inconsistent supplier request."""


class SupplierSimulatorError(SupplierError):
    """The synthetic fixture inventory or scenario is not usable."""


class SupplierNotFoundError(SupplierError):
    """The supplier, offer or booking reference is unknown."""


class OfferNotFoundError(SupplierNotFoundError):
    """The supplier has no offer with this reference."""

    def __init__(self, supplier: str, offer_id: str) -> None:
        self.supplier = supplier
        self.offer_id = offer_id
        super().__init__(f"{supplier} has no offer {offer_id}")


class OfferUnavailableError(SupplierError):
    """The offer exists but cannot be sold right now."""

    def __init__(self, supplier: str, offer_id: str, reason: str = "unavailable") -> None:
        self.supplier = supplier
        self.offer_id = offer_id
        self.reason = reason
        super().__init__(f"{supplier} offer {offer_id} is unavailable: {reason}")


class SupplierTimeoutError(SupplierError):
    """A supplier call exceeded its deadline. Retry only when ``retryable``."""

    def __init__(
        self,
        supplier: str,
        operation: str,
        offer_id: str | None = None,
        *,
        retryable: bool = True,
    ) -> None:
        self.supplier = supplier
        self.operation = operation
        self.offer_id = offer_id
        self.retryable = retryable
        target = f" for {offer_id}" if offer_id else ""
        super().__init__(f"{supplier} timed out on {operation}{target}")


class SupplierUnavailableError(SupplierError):
    """The supplier or portal cannot be used at all (access blocked, disabled).

    Portal adapters use this for an access-denied or anti-bot block: the run
    becomes ``unavailable`` and is not retried until an owner resolves access.
    """

    def __init__(self, supplier: str, reason: str) -> None:
        self.supplier = supplier
        self.reason = reason
        super().__init__(f"{supplier} is unavailable: {reason}")


class HoldNotFoundError(SupplierError):
    """The supplier has no hold with this reference."""

    def __init__(self, supplier: str, hold_id: str) -> None:
        self.supplier = supplier
        self.hold_id = hold_id
        super().__init__(f"{supplier} has no hold {hold_id}")


class HoldExpiredError(SupplierError):
    """A hold expired before it could be booked."""

    def __init__(self, supplier: str, hold_id: str, expires_at: datetime) -> None:
        self.supplier = supplier
        self.hold_id = hold_id
        self.expires_at = expires_at
        super().__init__(f"{supplier} hold {hold_id} expired at {expires_at.isoformat()}")


class BookingNotFoundError(SupplierNotFoundError):
    """The supplier has no booking with this reference."""

    def __init__(self, supplier: str, booking_ref: str) -> None:
        self.supplier = supplier
        self.booking_ref = booking_ref
        super().__init__(f"{supplier} has no booking {booking_ref}")


class BookingStateError(SupplierError):
    """The booking or hold does not allow this action in its current state."""


class IdempotencyConflictError(SupplierError):
    """A key was reused for a different request; never silently replay it."""

    def __init__(self, supplier: str, key: str) -> None:
        self.supplier = supplier
        self.key = key
        super().__init__(
            f"{supplier}: idempotency key {key!r} was already used for a different request"
        )
