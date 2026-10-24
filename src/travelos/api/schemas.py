"""Pydantic request/response models for the auth and admin API."""

from __future__ import annotations

import re
from datetime import datetime
from typing import Annotated
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictBool, field_validator

_EMAIL_PATTERN = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]{2,}$")


class _Credentials(BaseModel):
    model_config = ConfigDict(extra="forbid")

    email: Annotated[str, Field(min_length=3, max_length=320)]
    password: SecretStr

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value: str) -> str:
        normalized = value.strip().lower()
        if not _EMAIL_PATTERN.fullmatch(normalized):
            raise ValueError("valid email required")
        return normalized

    @field_validator("password")
    @classmethod
    def require_password(cls, value: SecretStr) -> SecretStr:
        if not value.get_secret_value():
            raise ValueError("password required")
        return value


class LoginRequest(_Credentials):
    totp_code: Annotated[str | None, Field(default=None, max_length=10)]


class MfaEnrollRequest(_Credentials):
    totp_code: Annotated[str | None, Field(default=None, max_length=10)]


class UserOut(BaseModel):
    id: UUID
    email: str
    role: str
    is_active: bool
    mfa_enrolled: bool


class LoginResponse(BaseModel):
    user: UserOut
    access_token: str
    expires_at: datetime
    csrf_token: str


class MfaEnrollResponse(BaseModel):
    secret: str
    otpauth_uri: str


class FlagOut(BaseModel):
    key: str
    enabled: bool
    source: str
    scope: str


class FlagUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    enabled: StrictBool
