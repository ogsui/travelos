"""Application configuration and runtime safety gates.

Two things are deliberately strict here:

* ``MODE`` defaults to ``test``. ``live`` refuses to start without an explicit
  ``LIVE_CONFIRMED=yes`` and every required production setting.
* Every outbound capability is a kill switch that defaults to ``False``.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

DEFAULT_DATABASE_URL = "postgresql+psycopg://travelos:travelos@localhost:5432/travelos"
DEFAULT_REDIS_URL = "redis://localhost:6379/0"
MIN_SECRET_LENGTH = 32
_PLACEHOLDER_SECRETS = frozenset(
    {
        "dev-only-not-a-secret-change-me",
        "change-me",
        "changeme",
        "secret",
        "test",
    }
)


class Mode(StrEnum):
    """Runtime mode. See docs/07_SECURITY_COMPLIANCE.md and AGENTS.md."""

    TEST = "test"
    SHADOW = "shadow"
    LIVE = "live"


class KillSwitches(BaseModel):
    """Per-capability kill switches. Every switch is OFF by default.

    Environment variables use a double underscore, e.g.
    ``KILL_SWITCHES__WHATSAPP=true``. Enabling a channel means the adapter is
    allowed to run; it does not bypass consent, approvals or mode checks.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    # Outbound / inbound channels
    whatsapp: bool = False
    email: bool = False
    sms: bool = False
    instagram: bool = False
    facebook: bool = False
    google_business: bool = False
    web_form: bool = False
    phone_log: bool = False

    # Acquisition and publishing
    cold_outreach: bool = False
    social_publishing: bool = False
    ads: bool = False

    # Fragile integrations (see AGENTS.md scraping rules)
    competitor_scraping: bool = False
    portal_automation: bool = False

    # Per-site competitor scrapers. Each site needs its own recorded decision.
    scraper_makemytrip: bool = False
    scraper_goibibo: bool = False
    scraper_booking_com: bool = False
    scraper_agoda: bool = False
    scraper_easemytrip: bool = False
    scraper_yatra: bool = False

    def enabled(self) -> tuple[str, ...]:
        """Return the names of enabled switches, in declaration order."""

        return tuple(name for name, value in self.model_dump().items() if value)

    @classmethod
    def switch_names(cls) -> tuple[str, ...]:
        """Every known kill switch. Used to validate admin flag endpoints."""

        return tuple(cls.model_fields)


class Settings(BaseSettings):
    """Settings loaded from environment variables and ``.env``.

    Production credentials are optional at definition time so the test and
    shadow modes can run without them. The live validator below makes them
    mandatory.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_nested_delimiter="__",
        extra="ignore",
        case_sensitive=False,
    )

    app_name: str = "TravelOS"
    mode: Mode = Mode.TEST
    live_confirmed: bool = False
    environment: str = "local"
    debug: bool = False

    # Synthetic data (task 007). The same seed must always produce the same
    # fixture bytes; changing it is a deliberate dataset change.
    synthetic_seed: int = Field(default=20261025, ge=0)

    timezone: str = "Asia/Kolkata"
    log_level: str = "INFO"
    log_json: bool = True

    # Infrastructure. Non-live modes fall back to the local Docker defaults.
    database_url: str | None = None
    redis_url: str | None = None
    secret_key: SecretStr | None = None
    public_base_url: str | None = None
    encryption_key: SecretStr | None = None

    cors_origins: list[str] = Field(default_factory=list)
    kill_switches: KillSwitches = Field(default_factory=KillSwitches)

    # Auth. Sessions are opaque server-side tokens, not JWTs (see DECISIONS.md).
    session_ttl_minutes: int = Field(default=720, ge=5, le=43_200)
    mfa_issuer: str = "TravelOS"

    # Background processing (task 004). The outbox is at-least-once; workers
    # retry with jittered exponential backoff and dead-letter after the limit.
    outbox_max_attempts: int = Field(default=5, ge=1, le=100)
    outbox_backoff_seconds: float = Field(default=10.0, gt=0, le=86_400)
    outbox_max_backoff_seconds: float = Field(default=3600.0, gt=0)
    outbox_jitter: float = Field(default=0.2, ge=0, le=1)
    outbox_batch_size: int = Field(default=25, ge=1, le=1000)
    outbox_poll_seconds: float = Field(default=5.0, gt=0, le=3600)

    # Timers: SLAs, expiries and reminders.
    timer_batch_size: int = Field(default=100, ge=1, le=1000)
    timer_poll_seconds: float = Field(default=10.0, gt=0, le=3600)

    # Approvals (task 005). These are safety defaults, not business policy:
    # an unanswered request escalates to the owner and then expires. The owner
    # can tune the timings through the environment.
    approval_ttl_minutes: int = Field(default=1440, ge=5, le=525_600)
    approval_escalation_minutes: int = Field(default=30, ge=1, le=525_600)

    # LLM gateway (task 006). The fake provider is the only default and may
    # never run in live mode. Real providers need the owner's key and model.
    prompts_dir: str = "prompts"
    llm_provider: str | None = None
    llm_provider_chain: list[str] = Field(default_factory=lambda: ["fake"])
    # Generic model overrides. The owner's preferred names are LLM_MODEL and
    # LLM_FALLBACK_MODEL; per-provider fields remain for compatibility.
    llm_model: str | None = None
    llm_fallback_model: str | None = None
    llm_max_attempts: int = Field(default=3, ge=1, le=10)
    llm_backoff_seconds: float = Field(default=1.0, gt=0, le=60)
    llm_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    # Unset means "no enforced cap" outside live. A value of 0 means "free
    # providers only" (a provider must declare pricing.free=True); the owner
    # must set a real paid budget before live paid calls can run.
    llm_daily_budget_paise: int | None = Field(default=None, ge=0)
    llm_accounting_timezone: str = "Asia/Kolkata"
    llm_call_retention_days: int = Field(default=90, ge=1, le=3650)
    llm_cache_enabled: bool = True
    llm_cache_ttl_seconds: int = Field(default=300, ge=1, le=86_400)
    llm_circuit_failure_threshold: int = Field(default=3, ge=1, le=100)
    llm_circuit_reset_seconds: int = Field(default=60, ge=1, le=3600)
    llm_fake_model: str = "fake-1"
    # OpenCode Zen free-tier chat models (official docs, 2026-10-24).
    llm_opencode_base_url: str = "https://opencode.ai/inference/openai/v1"
    llm_opencode_api_key: SecretStr | None = None
    llm_opencode_model: str = "deepseek-v4.1-flash-free"
    llm_opencode_fallback_model: str = "glm-5.3-flash-free"
    llm_opencode_review_model: str = "beam-free"
    llm_opencode_no_training: bool = True
    llm_opencode_synthetic_only: bool = False
    llm_openai_base_url: str = "https://api.openai.com/v1"
    llm_openai_model: str = ""
    llm_openai_api_key: SecretStr | None = None
    llm_openai_no_training: bool = True
    llm_openai_synthetic_only: bool = False
    llm_openai_input_paise_per_million: int = Field(default=0, ge=0)
    llm_openai_output_paise_per_million: int = Field(default=0, ge=0)
    llm_anthropic_base_url: str = "https://api.anthropic.com"
    llm_anthropic_model: str = ""
    llm_anthropic_api_key: SecretStr | None = None
    llm_anthropic_version: str = "2023-06-01"
    llm_anthropic_no_training: bool = True
    llm_anthropic_synthetic_only: bool = False
    llm_anthropic_input_paise_per_million: int = Field(default=0, ge=0)
    llm_anthropic_output_paise_per_million: int = Field(default=0, ge=0)
    llm_pricing_currency: str = "INR"

    @model_validator(mode="after")
    def _escalation_before_expiry(self) -> Settings:
        if self.approval_escalation_minutes >= self.approval_ttl_minutes:
            raise ValueError("APPROVAL_ESCALATION_MINUTES must be less than APPROVAL_TTL_MINUTES")
        return self

    @model_validator(mode="after")
    def _resolve_llm_provider(self) -> Settings:
        """Accept the owner's singular ``LLM_PROVIDER`` alias.

        ``LLM_PROVIDER=opencode`` is the documented switch; the list form stays
        available for multi-provider chains. The singular value wins when both
        are set so a stale list cannot silently override the owner's choice.
        """

        if self.llm_provider:
            provider = self.llm_provider.strip().lower()
            if not provider:
                raise ValueError("LLM_PROVIDER must not be empty")
            self.llm_provider_chain = [provider]
        try:
            ZoneInfo(self.llm_accounting_timezone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError(
                f"unknown LLM_ACCOUNTING_TIMEZONE: {self.llm_accounting_timezone!r}"
            ) from exc
        return self

    @model_validator(mode="after")
    def _enforce_live_safety(self) -> Settings:
        if self.mode is not Mode.LIVE:
            return self

        if not self.live_confirmed:
            raise ValueError(
                "MODE=live refused: set LIVE_CONFIRMED=yes only after the go-live checklist"
            )

        missing: list[str] = []
        if not self.database_url:
            missing.append("DATABASE_URL")
        if not self.redis_url:
            missing.append("REDIS_URL")
        if not self.public_base_url:
            missing.append("PUBLIC_BASE_URL")
        if not self.encryption_key:
            missing.append("ENCRYPTION_KEY")

        secret = self.secret_key.get_secret_value() if self.secret_key else ""
        if not secret:
            missing.append("SECRET_KEY")
        elif secret.lower() in _PLACEHOLDER_SECRETS:
            raise ValueError("MODE=live refused: SECRET_KEY is a known placeholder value")
        elif len(secret) < MIN_SECRET_LENGTH:
            raise ValueError(
                f"MODE=live refused: SECRET_KEY must be at least {MIN_SECRET_LENGTH} characters"
            )

        if missing:
            raise ValueError(
                "MODE=live refused: missing required settings: " + ", ".join(sorted(missing))
            )

        chain = [item.strip().lower() for item in self.llm_provider_chain if item.strip()]
        if not chain:
            raise ValueError("MODE=live refused: LLM_PROVIDER_CHAIN must not be empty")
        if "fake" in chain:
            raise ValueError("MODE=live refused: the fake LLM provider is test-only")
        llm_missing: list[str] = []
        for name in chain:
            if name == "opencode":
                if not self.llm_opencode_api_key:
                    llm_missing.append("LLM_OPENCODE_API_KEY")
                if not (self.llm_model or self.llm_opencode_model):
                    llm_missing.append("LLM_MODEL")
                if not self.llm_opencode_base_url.startswith("https://"):
                    raise ValueError("MODE=live refused: LLM_OPENCODE_BASE_URL must use https://")
            elif name in {"openai", "openai_compatible"}:
                if not self.llm_openai_api_key:
                    llm_missing.append("LLM_OPENAI_API_KEY")
                if not self.llm_openai_model:
                    llm_missing.append("LLM_OPENAI_MODEL")
                if not self.llm_openai_base_url.startswith("https://"):
                    raise ValueError("MODE=live refused: LLM_OPENAI_BASE_URL must use https://")
            elif name == "anthropic":
                if not self.llm_anthropic_api_key:
                    llm_missing.append("LLM_ANTHROPIC_API_KEY")
                if not self.llm_anthropic_model:
                    llm_missing.append("LLM_ANTHROPIC_MODEL")
                if not self.llm_anthropic_base_url.startswith("https://"):
                    raise ValueError("MODE=live refused: LLM_ANTHROPIC_BASE_URL must use https://")
            else:
                raise ValueError(f"MODE=live refused: unknown LLM provider {name!r}")
        if self.llm_daily_budget_paise is None:
            llm_missing.append("LLM_DAILY_BUDGET_PAISE")
        if not Path(self.prompts_dir).is_dir():
            llm_missing.append("PROMPTS_DIR")
        if llm_missing:
            raise ValueError(
                "MODE=live refused: missing required LLM settings: "
                + ", ".join(sorted(llm_missing))
            )

        if self.public_base_url and not self.public_base_url.startswith("https://"):
            raise ValueError("MODE=live refused: PUBLIC_BASE_URL must use https://")

        return self

    @property
    def effective_database_url(self) -> str:
        """Database URL, falling back to the local Docker default outside live."""

        return self.database_url or DEFAULT_DATABASE_URL

    @property
    def effective_redis_url(self) -> str:
        """Redis URL, falling back to the local Docker default outside live."""

        return self.redis_url or DEFAULT_REDIS_URL

    @property
    def is_live(self) -> bool:
        return self.mode is Mode.LIVE

    @property
    def secure_cookies(self) -> bool:
        """Cookies require TLS in live mode or whenever the public URL is https."""

        return self.is_live or (self.public_base_url or "").startswith("https://")


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return process-wide settings (cached)."""

    return Settings()


def reset_settings_cache() -> None:
    """Clear the settings cache (used by tests and config reloads)."""

    get_settings.cache_clear()
