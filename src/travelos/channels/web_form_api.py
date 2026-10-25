"""Public web-form submission endpoint."""

from __future__ import annotations

import re
from typing import Annotated, cast
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator
from sqlalchemy.orm import Session

from travelos.api.deps import get_db
from travelos.channels.web_form import (
    RateLimitStore,
    WebFormRateLimited,
    WebFormRateLimitUnavailable,
    WebFormService,
)

router = APIRouter(prefix="/channels", tags=["channels"])
_EMAIL_SHAPE = re.compile(r"^[^\s@]{1,64}@[^\s@.]{1,63}(?:\.[^\s@.]{1,63})+$")


class WebFormSubmissionIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    submission_id: UUID
    name: Annotated[str, Field(min_length=1, max_length=200)]
    email: Annotated[str | None, Field(default=None, max_length=320)]
    phone: Annotated[str | None, Field(default=None, max_length=40)]
    message: Annotated[str, Field(min_length=1, max_length=5000)]
    website: Annotated[str, Field(default="", max_length=500)]

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip()
        if not _EMAIL_SHAPE.fullmatch(normalized):
            raise ValueError("email address is invalid")
        return normalized

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str | None) -> str | None:
        if value is None:
            return None
        from travelos.crm.fingerprints import normalize_contact_value

        try:
            return normalize_contact_value("phone", value)
        except ValueError as exc:
            raise ValueError("phone number is invalid") from exc

    @field_validator("name", "message")
    @classmethod
    def trim_required_text(cls, value: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError("field must not be blank")
        return normalized

    @model_validator(mode="after")
    def require_contact(self) -> WebFormSubmissionIn:
        if self.email is None and self.phone is None:
            raise ValueError("provide an email address or phone number")
        return self


class WebFormSubmissionOut(BaseModel):
    accepted: bool = True
    duplicate: bool = False


def _rate_limiter(request: Request) -> RateLimitStore:
    return cast(RateLimitStore, request.app.state.web_form_rate_limiter)


@router.post("/web-form", response_model=WebFormSubmissionOut, status_code=status.HTTP_202_ACCEPTED)
def submit_web_form(
    payload: WebFormSubmissionIn,
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    limiter: Annotated[RateLimitStore, Depends(_rate_limiter)],
) -> WebFormSubmissionOut:
    # Honeypot responses intentionally match successful responses and create no records.
    if payload.website:
        return WebFormSubmissionOut(duplicate=False)
    source_ip = request.client.host if request.client is not None else "unknown"
    try:
        result = WebFormService(limiter).submit(
            db,
            submission_id=payload.submission_id,
            message=payload.message,
            source_ip=source_ip,
        )
    except WebFormRateLimited as exc:
        raise HTTPException(status_code=429, detail="submission_rate_limited") from exc
    except WebFormRateLimitUnavailable as exc:
        raise HTTPException(status_code=503, detail="submission_temporarily_unavailable") from exc
    db.commit()
    return WebFormSubmissionOut(duplicate=result.duplicate)
