"""Deterministic in-process provider used by tests, evals and CI.

It performs no network I/O. Tests can script exact responses and failures, so
schema repair, fallback and injection scenarios are reproducible.
"""

from __future__ import annotations

from collections.abc import Sequence

from travelos.llm.errors import ProviderError
from travelos.llm.types import (
    LLMRequest,
    LLMResponse,
    ProviderPolicy,
    ProviderPricing,
    estimate_tokens,
)


class FakeProvider:
    """A provider that returns queued responses instead of calling a model."""

    def __init__(
        self,
        *,
        name: str = "fake",
        model: str = "fake-1",
        responses: Sequence[str] | None = None,
        failures: Sequence[ProviderError | None] | None = None,
        policy: ProviderPolicy | None = None,
        pricing: ProviderPricing | None = None,
        default_response: str = '{"result": "ok"}',
    ) -> None:
        self.name = name
        self.model = model
        self.policy = policy or ProviderPolicy(no_training=True, synthetic_only=False)
        self.pricing = pricing or ProviderPricing()
        self._responses = list(responses or [])
        self._failures = list(failures or [])
        self._default_response = default_response
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        if self._failures:
            failure = self._failures.pop(0)
            if failure is not None:
                raise failure
        text = self._responses.pop(0) if self._responses else self._default_response
        prompt_chars = sum(len(message.content) for message in request.messages)
        return LLMResponse(
            text=text,
            input_tokens=estimate_tokens("x" * prompt_chars),
            output_tokens=estimate_tokens(text),
            model=self.model,
        )

    @property
    def calls(self) -> int:
        return len(self.requests)
