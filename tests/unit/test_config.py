"""Settings safety tests: modes, live gates and kill switches."""

from __future__ import annotations

import base64

import pytest
from pydantic import SecretStr, ValidationError

from travelos.core.config import (
    DEFAULT_DATABASE_URL,
    DEFAULT_REDIS_URL,
    KillSwitches,
    Mode,
    Settings,
)

LIVE_SECRET = "a" * 40
LIVE_BASE_URL = "https://travelos.example.com"
LIVE_ENCRYPTION_KEY = base64.urlsafe_b64encode(b"k" * 32).decode("ascii")


def live_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "mode": Mode.LIVE,
        "live_confirmed": True,
        "database_url": "postgresql+psycopg://user:pass@db:5432/travelos",
        "redis_url": "redis://redis:6379/0",
        "secret_key": SecretStr(LIVE_SECRET),
        "public_base_url": LIVE_BASE_URL,
        "encryption_key": SecretStr(LIVE_ENCRYPTION_KEY),
        "llm_provider_chain": ["openai"],
        "llm_openai_api_key": SecretStr("sk-synthetic-not-a-real-key"),
        "llm_openai_model": "owner-selected-model",
        "llm_daily_budget_paise": 1,
    }
    values.update(overrides)
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_defaults_are_safe() -> None:
    settings = Settings(_env_file=None)

    assert settings.mode is Mode.TEST
    assert settings.live_confirmed is False
    assert settings.kill_switches == KillSwitches()
    assert settings.kill_switches.enabled() == ()


def test_all_kill_switches_default_off() -> None:
    switches = KillSwitches()

    assert set(switches.model_dump().values()) == {False}
    assert "whatsapp" in switches.model_dump()


def test_kill_switch_can_be_enabled_by_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("KILL_SWITCHES__WHATSAPP", "true")
    monkeypatch.setenv("KILL_SWITCHES__SCRAPER_AGODA", "true")

    settings = Settings(_env_file=None)

    assert settings.kill_switches.whatsapp is True
    assert settings.kill_switches.scraper_agoda is True
    assert settings.kill_switches.enabled() == ("whatsapp", "scraper_agoda")


def test_unknown_kill_switch_is_rejected() -> None:
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        KillSwitches.model_validate({"whatsapp": True, "telepathy": True})


def test_shadow_mode_does_not_require_credentials() -> None:
    settings = Settings(_env_file=None, mode=Mode.SHADOW)

    assert settings.mode is Mode.SHADOW
    assert settings.effective_database_url == DEFAULT_DATABASE_URL
    assert settings.effective_redis_url == DEFAULT_REDIS_URL


def test_live_refuses_without_confirmation() -> None:
    with pytest.raises(ValidationError, match="LIVE_CONFIRMED=yes"):
        live_settings(live_confirmed=False)


def test_live_refuses_without_required_settings() -> None:
    with pytest.raises(ValidationError, match="missing required settings") as exc_info:
        Settings(_env_file=None, mode=Mode.LIVE, live_confirmed=True)

    message = str(exc_info.value)
    assert "DATABASE_URL" in message
    assert "REDIS_URL" in message
    assert "SECRET_KEY" in message
    assert "PUBLIC_BASE_URL" in message
    assert "ENCRYPTION_KEY" in message


def test_live_refuses_without_encryption_key() -> None:
    with pytest.raises(ValidationError, match="ENCRYPTION_KEY"):
        live_settings(encryption_key=None)


def test_live_key_is_not_revealed_by_repr() -> None:
    settings = live_settings()

    assert LIVE_ENCRYPTION_KEY not in repr(settings)


def test_live_refuses_short_secret() -> None:
    with pytest.raises(ValidationError, match="at least 32 characters"):
        live_settings(secret_key=SecretStr("too-short"))


def test_live_refuses_placeholder_secret() -> None:
    with pytest.raises(ValidationError, match="known placeholder"):
        live_settings(secret_key=SecretStr("changeme"))


def test_live_refuses_plain_http_public_url() -> None:
    with pytest.raises(ValidationError, match="https://"):
        live_settings(public_base_url="http://travelos.example.com")


def test_live_refuses_the_fake_llm_provider() -> None:
    with pytest.raises(ValidationError, match="fake LLM provider"):
        live_settings(llm_provider_chain=["fake"])


def test_live_requires_an_llm_budget() -> None:
    with pytest.raises(ValidationError, match="LLM_DAILY_BUDGET_PAISE"):
        live_settings(llm_daily_budget_paise=None)


def test_live_requires_the_provider_key_and_https_base_url() -> None:
    with pytest.raises(ValidationError, match="LLM_OPENAI_API_KEY"):
        live_settings(llm_openai_api_key=None)

    with pytest.raises(ValidationError, match="LLM_OPENAI_BASE_URL"):
        live_settings(llm_openai_base_url="http://llm.example.test/v1")


def test_live_accepts_fully_configured_settings() -> None:
    settings = live_settings()

    assert settings.is_live is True
    assert settings.effective_database_url == "postgresql+psycopg://user:pass@db:5432/travelos"
    assert LIVE_SECRET not in repr(settings)
