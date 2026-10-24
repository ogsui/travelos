"""Application configuration and runtime safety gates.

Two things are deliberately strict here:

* ``MODE`` defaults to ``test``. ``live`` refuses to start without an explicit
  ``LIVE_CONFIRMED=yes`` and every required production setting.
* Every outbound capability is a kill switch that defaults to ``False``.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache

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

    timezone: str = "Asia/Kolkata"
    log_level: str = "INFO"
    log_json: bool = True

    # Infrastructure. Non-live modes fall back to the local Docker defaults.
    database_url: str | None = None
    redis_url: str | None = None
    secret_key: SecretStr | None = None
    public_base_url: str | None = None

    cors_origins: list[str] = Field(default_factory=list)
    kill_switches: KillSwitches = Field(default_factory=KillSwitches)

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


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return process-wide settings (cached)."""

    return Settings()


def reset_settings_cache() -> None:
    """Clear the settings cache (used by tests and config reloads)."""

    get_settings.cache_clear()
