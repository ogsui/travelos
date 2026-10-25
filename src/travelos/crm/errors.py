"""Expected CRM domain errors."""

from __future__ import annotations

from travelos.core.errors import TravelOSError


class CRMRecordNotFoundError(TravelOSError):
    """A CRM row is missing or outside the caller's tenant."""


class CRMValidationError(TravelOSError):
    """A CRM operation violates a domain rule."""
