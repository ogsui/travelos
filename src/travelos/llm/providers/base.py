"""Provider contract shared by the fake and real adapters."""

from __future__ import annotations

from typing import Protocol

from travelos.llm.types import LLMRequest, LLMResponse, ProviderPolicy, ProviderPricing


class LLMProvider(Protocol):
    """One model endpoint with an explicit data policy and price list.

    Implementations must never log request bodies or credentials, must use a
    finite timeout, and must raise the typed errors from ``llm.errors``.
    """

    name: str
    model: str
    policy: ProviderPolicy
    pricing: ProviderPricing

    def complete(self, request: LLMRequest) -> LLMResponse:
        """Run one completion. No retries here; the gateway owns the chain."""
        ...
