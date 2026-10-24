"""Factory wiring: provider chain, policy flags and cache defaults."""

from __future__ import annotations

from pathlib import Path

import pytest

from travelos.core.config import Mode, Settings
from travelos.llm.errors import LLMConfigurationError
from travelos.llm.factory import build_gateway, build_provider_chain
from travelos.llm.providers.anthropic import AnthropicProvider
from travelos.llm.providers.fake import FakeProvider
from travelos.llm.providers.openai_compatible import OpenAICompatibleProvider
from travelos.llm.redaction import RedactionSession
from travelos.llm.types import DataClass


def settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "mode": Mode.TEST,
        "prompts_dir": str(tmp_path),
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_default_chain_is_the_deterministic_fake(tmp_path: Path) -> None:
    chain = build_provider_chain(settings(tmp_path))

    assert len(chain) == 1
    assert isinstance(chain[0], FakeProvider)
    assert chain[0].policy.accepts(DataClass.SYNTHETIC)
    assert chain[0].policy.no_training is True


def test_openai_and_anthropic_are_wired_with_policy_and_prices(tmp_path: Path) -> None:
    chain = build_provider_chain(
        settings(
            tmp_path,
            llm_provider_chain=["openai", "anthropic"],
            llm_openai_model="synthetic-model",
            llm_openai_api_key="synthetic-key",
            llm_openai_no_training=False,
            llm_openai_synthetic_only=True,
            llm_openai_input_paise_per_million=100,
            llm_openai_output_paise_per_million=200,
            llm_anthropic_model="claude-synthetic",
            llm_anthropic_api_key="synthetic-key",
        )
    )

    assert [provider.name for provider in chain] == ["openai", "anthropic"]
    openai = chain[0]
    assert isinstance(openai, OpenAICompatibleProvider)
    assert openai.policy.no_training is False
    assert openai.policy.synthetic_only is True
    assert openai.pricing.input_paise_per_million == 100
    assert openai.pricing.output_paise_per_million == 200
    anthropic = chain[1]
    assert isinstance(anthropic, AnthropicProvider)
    assert anthropic.policy.accepts(DataClass.CUSTOMER)
    assert anthropic.policy.accepts(DataClass.SYNTHETIC)


def test_unknown_provider_fails_loudly(tmp_path: Path) -> None:
    with pytest.raises(LLMConfigurationError, match="unknown LLM provider"):
        build_provider_chain(settings(tmp_path, llm_provider_chain=["mystery"]))


def test_empty_chain_is_rejected(tmp_path: Path) -> None:
    with pytest.raises(LLMConfigurationError, match="at least one provider"):
        build_provider_chain(settings(tmp_path, llm_provider_chain=[]))


def test_gateway_uses_a_dev_cache_outside_live(tmp_path: Path) -> None:
    gateway = build_gateway(settings(tmp_path, llm_cache_enabled=True))

    assert gateway.providers
    assert gateway._cache is not None


def test_cache_can_be_disabled(tmp_path: Path) -> None:
    gateway = build_gateway(settings(tmp_path, llm_cache_enabled=False))

    assert gateway._cache is None


def test_redaction_session_is_not_shared_between_calls() -> None:
    first = RedactionSession()
    second = RedactionSession()

    first.redact("asha@example.test")

    assert first.placeholders == ("[[EMAIL_1]]",)
    assert second.placeholders == ()
