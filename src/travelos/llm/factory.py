"""Build providers and the gateway from settings.

The default chain contains only the deterministic fake provider. Live mode
refuses the fake and requires owner-supplied keys, models, budgets and prices;
see ``core.config``.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import timedelta

from travelos.core.audit import AuditWriter
from travelos.core.clock import Clock
from travelos.core.config import Settings
from travelos.llm.cache import ResponseCache
from travelos.llm.errors import LLMConfigurationError
from travelos.llm.gateway import LLMGateway
from travelos.llm.prompts import PromptRegistry
from travelos.llm.providers.anthropic import AnthropicProvider
from travelos.llm.providers.base import LLMProvider
from travelos.llm.providers.fake import FakeProvider
from travelos.llm.providers.openai_compatible import OpenAICompatibleProvider
from travelos.llm.types import ProviderPolicy, ProviderPricing


def build_provider_chain(settings: Settings) -> list[LLMProvider]:
    providers: list[LLMProvider] = []
    for raw_name in settings.llm_provider_chain:
        name = raw_name.strip().lower()
        if not name:
            continue
        if name == "fake":
            if settings.is_live:
                raise LLMConfigurationError("the fake provider is not allowed in live mode")
            providers.append(FakeProvider(model=settings.llm_fake_model))
        elif name == "opencode":
            primary = _opencode_model(settings, settings.llm_model, settings.llm_opencode_model)
            providers.append(_opencode_provider(settings, name="opencode", model=primary))
            fallback_value = settings.llm_fallback_model
            if fallback_value is None:
                fallback_value = settings.llm_opencode_fallback_model
            fallback = fallback_value.strip()
            if fallback and fallback != primary:
                providers.append(
                    _opencode_provider(settings, name="opencode-fallback", model=fallback)
                )
        elif name in {"openai", "openai_compatible"}:
            providers.append(
                OpenAICompatibleProvider(
                    name="openai",
                    base_url=settings.llm_openai_base_url,
                    api_key=settings.llm_openai_api_key,
                    model=settings.llm_model or settings.llm_openai_model or "unconfigured-model",
                    policy=ProviderPolicy(
                        no_training=settings.llm_openai_no_training,
                        synthetic_only=settings.llm_openai_synthetic_only,
                    ),
                    pricing=ProviderPricing(
                        input_paise_per_million=settings.llm_openai_input_paise_per_million,
                        output_paise_per_million=settings.llm_openai_output_paise_per_million,
                        currency=settings.llm_pricing_currency,
                    ),
                    timeout_seconds=settings.llm_timeout_seconds,
                )
            )
        elif name == "anthropic":
            providers.append(
                AnthropicProvider(
                    name="anthropic",
                    base_url=settings.llm_anthropic_base_url,
                    api_key=settings.llm_anthropic_api_key,
                    model=settings.llm_model
                    or settings.llm_anthropic_model
                    or "unconfigured-model",
                    api_version=settings.llm_anthropic_version,
                    policy=ProviderPolicy(
                        no_training=settings.llm_anthropic_no_training,
                        synthetic_only=settings.llm_anthropic_synthetic_only,
                    ),
                    pricing=ProviderPricing(
                        input_paise_per_million=settings.llm_anthropic_input_paise_per_million,
                        output_paise_per_million=settings.llm_anthropic_output_paise_per_million,
                        currency=settings.llm_pricing_currency,
                    ),
                    timeout_seconds=settings.llm_timeout_seconds,
                )
            )
        else:
            raise LLMConfigurationError(f"unknown LLM provider in chain: {raw_name!r}")
    if not providers:
        raise LLMConfigurationError("LLM_PROVIDER_CHAIN must name at least one provider")
    return providers


def _opencode_model(settings: Settings, override: str | None, default: str) -> str:
    model = (override or default).strip()
    if not model:
        raise LLMConfigurationError("LLM_MODEL must name an OpenCode model")
    return model


def _opencode_provider(settings: Settings, *, name: str, model: str) -> OpenAICompatibleProvider:
    """One OpenCode Zen chat model behind the documented OpenAI-compatible API.

    Only models whose official ID ends in ``-free`` are declared free. Anything
    else stays unpriced, so a ``0`` budget blocks it instead of spending money
    silently. Paid OpenCode rates are not in this repository (BACKLOG).
    """

    return OpenAICompatibleProvider(
        name=name,
        base_url=settings.llm_opencode_base_url,
        api_key=settings.llm_opencode_api_key,
        model=model,
        policy=ProviderPolicy(
            no_training=settings.llm_opencode_no_training,
            synthetic_only=settings.llm_opencode_synthetic_only,
        ),
        pricing=ProviderPricing(
            currency=settings.llm_pricing_currency,
            free=model.endswith("-free"),
        ),
        timeout_seconds=settings.llm_timeout_seconds,
    )


def build_gateway(
    settings: Settings,
    *,
    providers: Sequence[LLMProvider] | None = None,
    clock: Clock | None = None,
    audit: AuditWriter | None = None,
) -> LLMGateway:
    chain = list(providers) if providers is not None else build_provider_chain(settings)
    cache = None
    if settings.llm_cache_enabled and not settings.is_live:
        cache = ResponseCache(
            ttl=timedelta(seconds=settings.llm_cache_ttl_seconds),
            clock=clock,
        )
    return LLMGateway(
        providers=chain,
        prompts=PromptRegistry(settings.prompts_dir),
        clock=clock,
        audit=audit,
        cache=cache,
        daily_budget_paise=settings.llm_daily_budget_paise,
        accounting_timezone=settings.llm_accounting_timezone,
        max_attempts=settings.llm_max_attempts,
        backoff_seconds=settings.llm_backoff_seconds,
        circuit_failure_threshold=settings.llm_circuit_failure_threshold,
        circuit_reset_seconds=settings.llm_circuit_reset_seconds,
    )
