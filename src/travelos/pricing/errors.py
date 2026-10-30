"""Expected pricing-domain errors.

A configuration error means the owner's rule file is malformed or incomplete in
a way that is not a simple placeholder. It never contains a secret or a price;
the message names the field and the rule that failed.
"""

from __future__ import annotations

from travelos.core.errors import TravelOSError


class PricingError(TravelOSError):
    """Base class for expected pricing failures."""


class PricingConfigError(PricingError):
    """The business-rule file is malformed or violates an invariant."""


class PricingValidationError(PricingError):
    """The caller supplied an inconsistent pricing request."""
