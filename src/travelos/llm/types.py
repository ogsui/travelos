"""Typed LLM-domain values shared by providers, the gateway and the database."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Literal
from uuid import UUID

MessageRole = Literal["system", "user", "assistant"]


class DataClass(StrEnum):
    """Data sensitivity handed to a provider. See docs/07_SECURITY_COMPLIANCE.md."""

    SYNTHETIC = "synthetic"
    CUSTOMER = "customer"
    PUBLIC = "public"

    @property
    def requires_no_training(self) -> bool:
        """Only synthetic data may go to a training-allowed or free-tier provider."""

        return self is not DataClass.SYNTHETIC

    @property
    def rank(self) -> int:
        """Sensitivity order used to stop a caller downgrading a prompt's class."""

        return {DataClass.SYNTHETIC: 0, DataClass.PUBLIC: 1, DataClass.CUSTOMER: 2}[self]


@dataclass(frozen=True, slots=True)
class ProviderPolicy:
    """What a provider contract permits. Defaults are the safe values."""

    no_training: bool = True
    synthetic_only: bool = False

    def accepts(self, data_class: DataClass) -> bool:
        if data_class is DataClass.SYNTHETIC:
            return True
        if self.synthetic_only:
            return False
        return self.no_training

    def describe(self) -> str:
        return (
            f"no_training={str(self.no_training).lower()}, "
            f"synthetic_only={str(self.synthetic_only).lower()}"
        )


@dataclass(frozen=True, slots=True)
class ProviderPricing:
    """Published price in integer paise per million tokens.

    Rates default to zero until the owner supplies a signed-off price list;
    inventing a price would silently corrupt the cost ledger. Cost rounds up to
    the next paise, so a call is never under-charged.

    ``free`` is an explicit declaration, never inferred from a zero rate:
    ``0`` normally means "not priced yet". A daily budget of ``0`` only routes
    to providers that declare ``free=True``, so an unpriced paid provider can
    never be used for free.
    """

    input_paise_per_million: int = 0
    output_paise_per_million: int = 0
    currency: str = "INR"
    free: bool = False

    def __post_init__(self) -> None:
        if self.input_paise_per_million < 0 or self.output_paise_per_million < 0:
            raise ValueError("token prices must not be negative")
        if self.free and (self.input_paise_per_million or self.output_paise_per_million):
            raise ValueError("a free provider must have zero input and output prices")
        if len(self.currency) != 3 or not self.currency.isalpha():
            raise ValueError("currency must be a three-letter code")

    def cost_paise(self, input_tokens: int, output_tokens: int) -> int:
        if input_tokens < 0 or output_tokens < 0:
            raise ValueError("token counts must not be negative")
        numerator = (
            input_tokens * self.input_paise_per_million
            + output_tokens * self.output_paise_per_million
        )
        return (numerator + 999_999) // 1_000_000


@dataclass(frozen=True, slots=True)
class LLMMessage:
    role: MessageRole
    content: str

    def __post_init__(self) -> None:
        if not self.content:
            raise ValueError("message content must not be empty")


@dataclass(frozen=True, slots=True)
class LLMRequest:
    """A provider call. There is deliberately no tool/function-call field."""

    messages: tuple[LLMMessage, ...]
    temperature: float = 0.0
    max_tokens: int = 1024

    def __post_init__(self) -> None:
        if not self.messages:
            raise ValueError("at least one message is required")
        if not 0.0 <= self.temperature <= 2.0:
            raise ValueError("temperature must be between 0 and 2")
        if self.max_tokens < 1:
            raise ValueError("max_tokens must be positive")

    @property
    def system_text(self) -> str:
        return "\n".join(message.content for message in self.messages if message.role == "system")


@dataclass(frozen=True, slots=True)
class LLMResponse:
    text: str
    input_tokens: int
    output_tokens: int
    model: str

    def __post_init__(self) -> None:
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError("token counts must not be negative")


@dataclass(frozen=True, slots=True)
class LLMResult:
    """What a caller receives after validation and restoration."""

    call_id: UUID | None
    text: str
    parsed: object | None
    provider: str
    model: str
    prompt_name: str
    prompt_version: int
    data_class: DataClass
    input_tokens: int
    output_tokens: int
    cost_paise: int
    currency: str
    latency_ms: int
    cached: bool
    repairs: int


def estimate_tokens(text: str) -> int:
    """Cheap deterministic estimate used only by the fake provider."""

    if not text:
        return 0
    return max(1, len(text) // 4)


def merge_messages(
    first: Sequence[LLMMessage], second: Sequence[LLMMessage]
) -> tuple[LLMMessage, ...]:
    return tuple(first) + tuple(second)
