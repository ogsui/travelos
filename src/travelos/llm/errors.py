"""Typed LLM errors. Adapters raise these; the gateway decides what to do."""

from __future__ import annotations

from travelos.llm.types import DataClass


class LLMError(Exception):
    """Base class for every LLM failure."""


class LLMConfigurationError(LLMError):
    """The provider chain or prompt configuration cannot be used."""


class PromptNotFoundError(LLMError):
    def __init__(self, name: str, version: int | None = None) -> None:
        self.name = name
        self.version = version
        super().__init__(f"prompt not found: {name!r}" + (f" version {version}" if version else ""))


class PromptFormatError(LLMError):
    """A prompt file is malformed or has an unsafe name/version."""


class PromptRenderError(LLMError):
    """A template variable is missing or a template is invalid."""


class LLMPolicyViolationError(LLMError):
    """A provider's policy does not permit this data class."""

    def __init__(self, data_class: DataClass, providers: tuple[str, ...]) -> None:
        self.data_class = data_class
        self.providers = providers
        super().__init__(
            f"no provider accepts {data_class.value} data: {', '.join(providers) or 'none'}"
        )


class LLMBudgetExceededError(LLMError):
    def __init__(self, spent_paise: int, budget_paise: int) -> None:
        self.spent_paise = spent_paise
        self.budget_paise = budget_paise
        super().__init__(f"daily LLM budget exhausted ({spent_paise}/{budget_paise} paise)")


class LLMSchemaError(LLMError):
    """The model twice returned output that failed the caller's schema."""

    def __init__(self, prompt_name: str, summary: str) -> None:
        self.prompt_name = prompt_name
        self.summary = summary
        super().__init__(f"{prompt_name}: invalid structured output: {summary}")


class AllProvidersFailedError(LLMError):
    def __init__(self, failures: tuple[str, ...]) -> None:
        self.failures = failures
        super().__init__("all LLM providers failed: " + "; ".join(failures))


class ProviderError(LLMError):
    """Base class for provider transport failures."""

    def __init__(self, provider: str, message: str) -> None:
        self.provider = provider
        super().__init__(f"{provider}: {message}")


class TransientProviderError(ProviderError):
    """Timeout, connection reset or 5xx. Safe to retry."""


class RateLimitedProviderError(ProviderError):
    """429. Safe to retry after a delay."""


class AuthProviderError(ProviderError):
    """401/403. Retrying will not help; try the next provider."""


class PermanentProviderError(ProviderError):
    """A 4xx that retrying cannot fix."""
