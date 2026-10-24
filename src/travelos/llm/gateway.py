"""The single entry point for every LLM call.

Responsibilities, in order:

1. load the versioned prompt and its declared data class;
2. enforce provider policy against that data class;
3. redact PII into reversible placeholders and delimit untrusted values;
4. serve a development cache hit, if allowed;
5. check the owner's daily budget;
6. try providers in order, with transport retries and a circuit breaker;
7. validate structured output, ask for exactly one repair, then fall back;
8. restore placeholders and write ``llm_calls``, audit and human tasks.

Money is never computed by a model. Costs here come from the provider's
published integer-paise price list; unpriced providers default to zero until the
owner supplies a price list.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from time import perf_counter
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from travelos.core.audit import Actor, AuditWriter, jsonable
from travelos.core.clock import Clock, SystemClock
from travelos.core.db import DEFAULT_TENANT_ID
from travelos.core.models import HumanTask
from travelos.core.redaction import redact_text
from travelos.llm.cache import CachedResponse, ResponseCache, canonical
from travelos.llm.circuit import CircuitBreaker
from travelos.llm.errors import (
    AllProvidersFailedError,
    AuthProviderError,
    LLMBudgetExceededError,
    LLMPolicyViolationError,
    LLMSchemaError,
    PermanentProviderError,
    RateLimitedProviderError,
    TransientProviderError,
)
from travelos.llm.injection import check_allow_list
from travelos.llm.models import LLMCall
from travelos.llm.prompts import PromptRegistry, PromptSpec
from travelos.llm.providers.base import LLMProvider
from travelos.llm.redaction import RedactionSession
from travelos.llm.schema import parse_json_output, validate_json
from travelos.llm.types import (
    DataClass,
    LLMMessage,
    LLMRequest,
    LLMResponse,
    LLMResult,
)

logger = logging.getLogger(__name__)

_MAX_TITLE = 200
_MAX_ERROR = 2000


@dataclass(slots=True)
class _Attempt:
    """Result of one provider plus any repair call."""

    success: bool
    text: str = ""
    parsed: object | None = None
    response: LLMResponse | None = None
    call_id: UUID | None = None
    errors: tuple[str, ...] = ()
    repairs: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cost_paise: int = 0
    latency_ms: int = 0
    saw_output: bool = False


class LLMGateway:
    """Provider chain, prompt registry, policy, validation and accounting."""

    def __init__(
        self,
        *,
        providers: Sequence[LLMProvider],
        prompts: PromptRegistry,
        clock: Clock | None = None,
        audit: AuditWriter | None = None,
        cache: ResponseCache | None = None,
        daily_budget_paise: int | None = None,
        max_attempts: int = 3,
        backoff_seconds: float = 1.0,
        circuit_failure_threshold: int = 3,
        circuit_reset_seconds: int = 60,
        sleep: Callable[[float], None] | None = None,
    ) -> None:
        if not providers:
            raise ValueError("at least one LLM provider is required")
        if max_attempts < 1:
            raise ValueError("max_attempts must be >= 1")
        if backoff_seconds <= 0:
            raise ValueError("backoff_seconds must be positive")
        if daily_budget_paise is not None and daily_budget_paise < 0:
            raise ValueError("daily_budget_paise must not be negative")
        self._providers = tuple(providers)
        self._prompts = prompts
        self._clock = clock or SystemClock()
        self._audit = audit or AuditWriter()
        self._cache = cache
        self._budget = daily_budget_paise
        self._max_attempts = max_attempts
        self._backoff_seconds = backoff_seconds
        self._circuit_threshold = circuit_failure_threshold
        self._circuit_reset = timedelta(seconds=circuit_reset_seconds)
        self._sleep = sleep or time.sleep
        self._breakers: dict[str, CircuitBreaker] = {}

    @property
    def providers(self) -> tuple[LLMProvider, ...]:
        return self._providers

    @property
    def prompts(self) -> PromptRegistry:
        return self._prompts

    def complete(
        self,
        db: Session,
        *,
        prompt_name: str,
        variables: Mapping[str, object] | None = None,
        version: int | None = None,
        data_class: DataClass | None = None,
        tenant_id: UUID | None = None,
        actor_id: UUID | None = None,
        use_cache: bool | None = None,
        allow: Mapping[str, Sequence[str]] | None = None,
    ) -> LLMResult:
        """Run one prompt and return validated, restored output."""

        tenant = tenant_id or DEFAULT_TENANT_ID
        spec = self._prompts.get(prompt_name, version)
        chosen_class = data_class or spec.data_class
        if chosen_class.rank < spec.data_class.rank:
            row = self._record_call(
                db,
                tenant=tenant,
                spec=spec,
                data_class=chosen_class,
                status="policy_blocked",
                request_payload={},
                provider=None,
                response=None,
                error=(
                    f"prompt declares {spec.data_class.value} data; "
                    f"a call may not downgrade it to {chosen_class.value}"
                ),
                latency_ms=0,
                cached=False,
                repairs=0,
                actor_id=actor_id,
            )
            self._human_task(
                db,
                tenant,
                row,
                task_type="llm.policy_blocked",
                title=f"LLM data-class downgrade refused: {spec.name} v{spec.version}",
            )
            raise LLMPolicyViolationError(
                chosen_class, (f"prompt declares {spec.data_class.value}",)
            )
        redactor = RedactionSession()
        request = self._build_request(spec, variables or {}, redactor)
        request_payload = _request_payload(request)
        eligible = tuple(p for p in self._providers if p.policy.accepts(chosen_class))

        if not eligible:
            providers = tuple(f"{p.name} ({p.policy.describe()})" for p in self._providers)
            row = self._record_call(
                db,
                tenant=tenant,
                spec=spec,
                data_class=chosen_class,
                status="policy_blocked",
                request_payload=request_payload,
                provider=None,
                response=None,
                error=f"no provider accepts {chosen_class.value} data",
                latency_ms=0,
                cached=False,
                repairs=0,
                actor_id=actor_id,
            )
            self._human_task(
                db,
                tenant,
                row,
                task_type="llm.policy_blocked",
                title=f"LLM policy blocked {spec.name} v{spec.version}: {chosen_class.value}",
            )
            raise LLMPolicyViolationError(chosen_class, providers)

        cache_key = None
        if self._cache is not None and use_cache is not False:
            cache_key = self._cache_key(tenant, spec, chosen_class, request)
            cached = self._cache.get(cache_key)
            if cached is not None:
                return self._cached_result(
                    db,
                    tenant=tenant,
                    spec=spec,
                    data_class=chosen_class,
                    request_payload=request_payload,
                    entry=cached,
                    redactor=redactor,
                    actor_id=actor_id,
                )

        spent = self._spent_today(db, tenant)
        if self._budget is not None and spent >= self._budget:
            row = self._record_call(
                db,
                tenant=tenant,
                spec=spec,
                data_class=chosen_class,
                status="budget_blocked",
                request_payload=request_payload,
                provider=None,
                response=None,
                error=f"daily budget exhausted ({spent}/{self._budget} paise)",
                latency_ms=0,
                cached=False,
                repairs=0,
                actor_id=actor_id,
            )
            self._human_task(
                db,
                tenant,
                row,
                task_type="llm.budget_exceeded",
                title=f"LLM daily budget exhausted for {spec.name} v{spec.version}",
            )
            raise LLMBudgetExceededError(spent, self._budget)

        failures: list[str] = []
        total_cost = 0
        total_input = 0
        total_output = 0
        repairs_total = 0
        saw_output = False

        for provider in eligible:
            breaker = self._breaker(provider)
            if not breaker.allows():
                failures.append(f"{provider.name}: circuit open")
                continue
            try:
                attempt = self._attempt_provider(
                    db,
                    tenant=tenant,
                    spec=spec,
                    data_class=chosen_class,
                    provider=provider,
                    request=request,
                    allow=_merge_allow(spec, allow),
                    actor_id=actor_id,
                )
            except (AuthProviderError, PermanentProviderError) as exc:
                breaker.record_failure()
                failures.append(str(exc))
                continue
            except (TransientProviderError, RateLimitedProviderError) as exc:
                breaker.record_failure()
                failures.append(str(exc))
                continue

            total_cost += attempt.cost_paise
            total_input += attempt.input_tokens
            total_output += attempt.output_tokens
            repairs_total += attempt.repairs
            saw_output = saw_output or attempt.saw_output
            if attempt.success:
                breaker.record_success()
                if self._cache is not None and cache_key is not None and use_cache is not False:
                    self._cache.put(
                        cache_key,
                        CachedResponse(
                            provider=provider.name,
                            model=attempt.response.model if attempt.response else provider.model,
                            text=attempt.text,
                            parsed=attempt.parsed,
                            input_tokens=attempt.input_tokens,
                            output_tokens=attempt.output_tokens,
                            repairs=attempt.repairs,
                            currency=provider.pricing.currency,
                        ),
                    )
                response = attempt.response
                return LLMResult(
                    call_id=attempt.call_id,
                    text=redactor.restore(attempt.text),
                    parsed=redactor.restore_value(attempt.parsed),
                    provider=provider.name,
                    model=response.model if response else provider.model,
                    prompt_name=spec.name,
                    prompt_version=spec.version,
                    data_class=chosen_class,
                    input_tokens=total_input,
                    output_tokens=total_output,
                    cost_paise=total_cost,
                    currency=provider.pricing.currency,
                    latency_ms=attempt.latency_ms,
                    cached=False,
                    repairs=repairs_total,
                )
            breaker.record_success()  # reachable; the output was wrong, not the transport
            failures.append(
                f"{provider.name}: invalid structured output "
                f"({'; '.join(attempt.errors)[:300] or 'unknown'})"
            )

        summary = "; ".join(failures) or "no provider available"
        row = self._record_call(
            db,
            tenant=tenant,
            spec=spec,
            data_class=chosen_class,
            status="schema_error" if saw_output else "all_providers_failed",
            request_payload=request_payload,
            provider=None,
            response=None,
            error=summary,
            latency_ms=0,
            cached=False,
            repairs=repairs_total,
            actor_id=actor_id,
            metadata={"failures": failures, "cost_paise": total_cost},
        )
        self._human_task(
            db,
            tenant,
            row,
            task_type="llm.schema_failure" if saw_output else "llm.provider_failure",
            title=(
                f"LLM output failed validation: {spec.name} v{spec.version}"
                if saw_output
                else f"All LLM providers failed: {spec.name} v{spec.version}"
            ),
        )
        if saw_output:
            raise LLMSchemaError(spec.name, summary)
        raise AllProvidersFailedError(tuple(failures))

    # -- request construction --------------------------------------------

    def _build_request(
        self,
        spec: PromptSpec,
        variables: Mapping[str, object],
        redactor: RedactionSession,
    ) -> LLMRequest:
        redacted: dict[str, object] = {}
        for key, value in variables.items():
            text = _as_text(value)
            if key in spec.sensitive:
                redacted[key] = redactor.redact_value(text, label=key)
            else:
                redacted[key] = redactor.redact(text)
        rendered = spec.render(redacted)
        messages = tuple(
            LLMMessage(
                role="system" if role.lower() == "system" else "user",
                content=content,
            )
            for role, content in rendered
        )
        return LLMRequest(
            messages=messages,
            temperature=spec.temperature,
            max_tokens=spec.max_tokens,
        )

    def _attempt_provider(
        self,
        db: Session,
        *,
        tenant: UUID,
        spec: PromptSpec,
        data_class: DataClass,
        provider: LLMProvider,
        request: LLMRequest,
        allow: Mapping[str, Sequence[str]] | None,
        actor_id: UUID | None,
    ) -> _Attempt:
        attempt = _Attempt(success=False)
        current = request
        for repair_index in range(2):  # initial call plus at most one repair
            response, latency_ms = self._call_with_retries(
                db,
                tenant=tenant,
                spec=spec,
                data_class=data_class,
                provider=provider,
                request=current,
                actor_id=actor_id,
                repairs=attempt.repairs,
            )
            attempt.response = response
            attempt.latency_ms += latency_ms
            attempt.input_tokens += response.input_tokens
            attempt.output_tokens += response.output_tokens
            attempt.cost_paise += provider.pricing.cost_paise(
                response.input_tokens, response.output_tokens
            )
            parsed, errors = _validate_output(response.text, spec, allow)
            if not errors:
                row = self._record_call(
                    db,
                    tenant=tenant,
                    spec=spec,
                    data_class=data_class,
                    status="success",
                    request_payload=_request_payload(current),
                    provider=provider,
                    response=response,
                    error=None,
                    latency_ms=latency_ms,
                    cached=False,
                    repairs=attempt.repairs,
                    actor_id=actor_id,
                )
                attempt.success = True
                attempt.text = response.text
                attempt.parsed = parsed
                attempt.call_id = row.id
                attempt.saw_output = True
                return attempt

            attempt.saw_output = True
            self._record_call(
                db,
                tenant=tenant,
                spec=spec,
                data_class=data_class,
                status="schema_error",
                request_payload=_request_payload(current),
                provider=provider,
                response=response,
                error="; ".join(errors),
                latency_ms=latency_ms,
                cached=False,
                repairs=attempt.repairs,
                actor_id=actor_id,
            )
            if repair_index == 1:
                attempt.errors = tuple(errors)
                return attempt
            attempt.repairs += 1
            current = _repair_request(request, response.text, errors, spec)
        attempt.errors = ("repair loop ended unexpectedly",)
        return attempt

    def _call_with_retries(
        self,
        db: Session,
        *,
        tenant: UUID,
        spec: PromptSpec,
        data_class: DataClass,
        provider: LLMProvider,
        request: LLMRequest,
        actor_id: UUID | None,
        repairs: int,
    ) -> tuple[LLMResponse, int]:
        last: TransientProviderError | RateLimitedProviderError | None = None
        for attempt in range(1, self._max_attempts + 1):
            started = perf_counter()
            try:
                response = provider.complete(request)
            except (TransientProviderError, RateLimitedProviderError) as exc:
                last = exc
                self._record_call(
                    db,
                    tenant=tenant,
                    spec=spec,
                    data_class=data_class,
                    status="provider_error",
                    request_payload=_request_payload(request),
                    provider=provider,
                    response=None,
                    error=str(exc),
                    latency_ms=_elapsed_ms(started),
                    cached=False,
                    repairs=repairs,
                    actor_id=actor_id,
                    metadata={"attempt": attempt},
                )
                if attempt < self._max_attempts:
                    self._sleep(self._delay(attempt))
                continue
            except (AuthProviderError, PermanentProviderError) as exc:
                self._record_call(
                    db,
                    tenant=tenant,
                    spec=spec,
                    data_class=data_class,
                    status="provider_error",
                    request_payload=_request_payload(request),
                    provider=provider,
                    response=None,
                    error=str(exc),
                    latency_ms=_elapsed_ms(started),
                    cached=False,
                    repairs=repairs,
                    actor_id=actor_id,
                    metadata={"attempt": attempt},
                )
                raise
            except Exception as exc:  # a provider bug must not take the gateway down
                self._record_call(
                    db,
                    tenant=tenant,
                    spec=spec,
                    data_class=data_class,
                    status="provider_error",
                    request_payload=_request_payload(request),
                    provider=provider,
                    response=None,
                    error=f"unexpected {type(exc).__name__}",
                    latency_ms=_elapsed_ms(started),
                    cached=False,
                    repairs=repairs,
                    actor_id=actor_id,
                    metadata={"attempt": attempt},
                )
                raise PermanentProviderError(
                    provider.name, f"unexpected provider failure ({type(exc).__name__})"
                ) from exc
            return response, _elapsed_ms(started)
        if last is not None:
            raise last
        raise PermanentProviderError(provider.name, "provider returned no response")

    def _delay(self, attempt: int) -> float:
        return min(self._backoff_seconds * float(2 ** (attempt - 1)), 60.0)

    # -- cache and budget -------------------------------------------------

    def _cache_key(
        self,
        tenant: UUID,
        spec: PromptSpec,
        data_class: DataClass,
        request: LLMRequest,
    ) -> str:
        parts = {
            "tenant": str(tenant),
            "prompt": spec.name,
            "version": spec.version,
            "data_class": data_class.value,
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "messages": [
                {"role": message.role, "content": message.content} for message in request.messages
            ],
        }
        return sha256(canonical(parts).encode("utf-8")).hexdigest()

    def _cached_result(
        self,
        db: Session,
        *,
        tenant: UUID,
        spec: PromptSpec,
        data_class: DataClass,
        request_payload: Mapping[str, Any],
        entry: CachedResponse,
        redactor: RedactionSession,
        actor_id: UUID | None,
    ) -> LLMResult:
        row = self._record_call(
            db,
            tenant=tenant,
            spec=spec,
            data_class=data_class,
            status="success",
            request_payload=request_payload,
            provider=None,
            response=LLMResponse(
                text=entry.text,
                input_tokens=entry.input_tokens,
                output_tokens=entry.output_tokens,
                model=entry.model,
            ),
            error=None,
            latency_ms=0,
            cached=True,
            repairs=entry.repairs,
            actor_id=actor_id,
            metadata={"cached": True, "provider": entry.provider},
        )
        return LLMResult(
            call_id=row.id,
            text=redactor.restore(entry.text),
            parsed=redactor.restore_value(entry.parsed),
            provider=entry.provider,
            model=entry.model,
            prompt_name=spec.name,
            prompt_version=spec.version,
            data_class=data_class,
            input_tokens=0,
            output_tokens=0,
            cost_paise=0,
            currency=entry.currency,
            latency_ms=0,
            cached=True,
            repairs=entry.repairs,
        )

    def _spent_today(self, db: Session, tenant: UUID) -> int:
        start = utc_day_start(self._clock.now())
        total = db.execute(
            select(func.coalesce(func.sum(LLMCall.cost_paise), 0)).where(
                LLMCall.tenant_id == tenant,
                LLMCall.created_at >= start,
            )
        ).scalar_one()
        return int(total)

    # -- persistence ------------------------------------------------------

    def _record_call(
        self,
        db: Session,
        *,
        tenant: UUID,
        spec: PromptSpec,
        data_class: DataClass,
        status: str,
        request_payload: Mapping[str, Any],
        provider: LLMProvider | None,
        response: LLMResponse | None,
        error: str | None,
        latency_ms: int,
        cached: bool,
        repairs: int,
        actor_id: UUID | None,
        metadata: Mapping[str, Any] | None = None,
    ) -> LLMCall:
        request_safe = _redact_payload(request_payload)
        response_safe: dict[str, Any] | None = None
        if response is not None:
            response_safe = {"text": redact_text(response.text), "model": response.model}
        error_safe = redact_text(error)[:_MAX_ERROR] if error else None
        cost = (
            provider.pricing.cost_paise(response.input_tokens, response.output_tokens)
            if provider is not None and response is not None
            else 0
        )
        row = LLMCall(
            tenant_id=tenant,
            actor_id=actor_id,
            provider=provider.name if provider is not None else None,
            model=(response.model if response is not None else None)
            or (provider.model if provider is not None else None),
            prompt_name=spec.name,
            prompt_version=spec.version,
            data_class=data_class.value,
            status=status,
            input_tokens=response.input_tokens if response is not None else 0,
            output_tokens=response.output_tokens if response is not None else 0,
            cost_paise=cost,
            currency=provider.pricing.currency if provider is not None else "INR",
            latency_ms=latency_ms,
            cached=cached,
            repairs=repairs,
            request=request_safe,
            response=response_safe,
            request_hash=sha256(canonical(request_safe).encode("utf-8")).hexdigest(),
            response_hash=(
                sha256(canonical(response_safe).encode("utf-8")).hexdigest()
                if response_safe is not None
                else None
            ),
            error=error_safe,
            metadata_json=jsonable(dict(metadata or {})),
        )
        db.add(row)
        db.flush()
        self._audit.record(
            db,
            action="llm.call_succeeded" if status == "success" else "llm.call_failed",
            actor=Actor.ai(),
            subject_type="llm_call",
            subject_id=row.id,
            after={
                "provider": row.provider,
                "model": row.model,
                "prompt": spec.name,
                "version": spec.version,
                "data_class": data_class.value,
                "status": status,
                "input_tokens": row.input_tokens,
                "output_tokens": row.output_tokens,
                "cost_paise": row.cost_paise,
                "currency": row.currency,
                "cached": cached,
                "repairs": repairs,
            },
        )
        return row

    def _human_task(
        self,
        db: Session,
        tenant: UUID,
        row: LLMCall,
        *,
        task_type: str,
        title: str,
    ) -> None:
        db.add(
            HumanTask(
                tenant_id=tenant,
                task_type=task_type,
                subject_type="llm_call",
                subject_id=row.id,
                title=title[:_MAX_TITLE],
                assignee_role="owner",
                priority="high",
            )
        )

    def _breaker(self, provider: LLMProvider) -> CircuitBreaker:
        key = f"{provider.name}:{provider.model}"
        breaker = self._breakers.get(key)
        if breaker is None:
            breaker = CircuitBreaker(
                failure_threshold=self._circuit_threshold,
                reset_after=self._circuit_reset,
                clock=self._clock,
            )
            self._breakers[key] = breaker
        return breaker


def _request_payload(request: LLMRequest) -> dict[str, Any]:
    return {
        "messages": [
            {"role": message.role, "content": message.content} for message in request.messages
        ],
        "temperature": request.temperature,
        "max_tokens": request.max_tokens,
    }


def _redact_payload(payload: Mapping[str, Any]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in payload.items():
        if isinstance(value, str):
            result[key] = redact_text(value)
        elif isinstance(value, list):
            result[key] = [_redact_payload_item(item) for item in value]
        elif isinstance(value, Mapping):
            result[key] = _redact_payload(value)
        else:
            result[key] = value
    return result


def _redact_payload_item(value: object) -> object:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return _redact_payload(value)
    if isinstance(value, list):
        return [_redact_payload_item(item) for item in value]
    return value


def _validate_output(
    text: str,
    spec: PromptSpec,
    allow: Mapping[str, Sequence[str]] | None,
) -> tuple[object | None, list[str]]:
    if spec.output_schema is None:
        return None, []
    parsed = parse_json_output(text)
    if parsed is None:
        return None, ["output is not valid JSON"]
    errors = validate_json(parsed, spec.output_schema)
    if allow:
        errors.extend(check_allow_list(parsed, allow))
    return parsed, errors


def _repair_request(
    original: LLMRequest,
    previous_output: str,
    errors: Sequence[str],
    spec: PromptSpec,
) -> LLMRequest:
    summary = "; ".join(errors)[:_MAX_ERROR] or "unknown validation error"
    schema = canonical(spec.output_schema) if spec.output_schema is not None else "{}"
    instruction = (
        "Your previous response did not satisfy the required JSON schema.\n"
        "Return ONLY corrected JSON. Do not add commentary, markdown fences or explanations.\n"
        "You cannot call tools, create links or take actions.\n\n"
        f"Validation errors:\n{summary}\n\n"
        f"Required schema:\n{schema}"
    )
    messages = (
        *original.messages,
        LLMMessage(role="assistant", content=previous_output),
        LLMMessage(role="user", content=instruction),
    )
    return LLMRequest(
        messages=messages,
        temperature=original.temperature,
        max_tokens=original.max_tokens,
    )


def _merge_allow(
    spec: PromptSpec, extra: Mapping[str, Sequence[str]] | None
) -> Mapping[str, Sequence[str]] | None:
    if spec.allow is None:
        return extra
    if extra is None:
        return spec.allow
    merged = {key: tuple(values) for key, values in spec.allow.items()}
    for key, values in extra.items():
        merged[key] = tuple(values)
    return merged


def _as_text(value: object) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool | int | float):
        return str(value)
    return canonical(value)


def _elapsed_ms(started: float) -> int:
    return max(0, int((perf_counter() - started) * 1000))


def utc_day_start(now: datetime) -> datetime:
    """Exported for tests and budget dashboards."""

    return now.astimezone(UTC).replace(hour=0, minute=0, second=0, microsecond=0)
