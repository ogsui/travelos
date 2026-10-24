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


class ApprovalSubmitRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action_type: Annotated[str, Field(min_length=3, max_length=120)]
    subject_type: Annotated[str, Field(min_length=1, max_length=80)]
    subject_id: UUID | None = None
    proposed: dict[str, object] = Field(default_factory=dict)
    reason: Annotated[str | None, Field(default=None, max_length=2000)]
    value_paise: Annotated[int, Field(default=0, ge=0)]
    context: dict[str, object] = Field(default_factory=dict)


class ApprovalDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decision: Annotated[str, Field(pattern="^(approve|edit|reject)$")]
    final: dict[str, object] | None = None
    reason: Annotated[str | None, Field(default=None, max_length=2000)]


class ApprovalOut(BaseModel):
    id: UUID
    action_type: str
    subject_type: str
    subject_id: UUID | None
    requested_by: UUID | None
    status: str
    level: str
    proposed: dict[str, object]
    final: dict[str, object] | None
    reason: str | None
    requires_four_eyes: bool
    approvals_needed: int
    approvals_count: int
    reviewer_id: UUID | None
    second_reviewer_id: UUID | None
    expires_at: datetime | None
    escalated_at: datetime | None
    decided_at: datetime | None
    created_at: datetime

    @classmethod
    def from_model(cls, request: object) -> ApprovalOut:
        return cls.model_validate(request, from_attributes=True)


class PolicyDecisionOut(BaseModel):
    action_type: str
    decision: str
    level: str
    configured_level: str
    max_level: str
    locked: bool
    owner_only: bool
    requires_four_eyes: bool
    review_after: bool
    reason: str


class ApprovalSubmitResponse(BaseModel):
    decision: PolicyDecisionOut
    approval: ApprovalOut | None


class AutonomyPolicyOut(BaseModel):
    action_type: str
    level: str
    default_level: str
    max_level: str
    locked: bool
    owner_only: bool
    may_graduate: bool
    value_threshold_paise: int
    currency: str
    requires_four_eyes: bool
    notes: str | None
    source: str


class AutonomyPolicyUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: Annotated[str, Field(pattern="^A[0-3]$")]
    value_threshold_paise: Annotated[int, Field(default=0, ge=0)]
    requires_four_eyes: StrictBool = False
    notes: Annotated[str | None, Field(default=None, max_length=2000)]
