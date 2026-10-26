"""Gateway-backed inquiry parser (task 010b).

The model returns a requirement plus, for each filled field, a confidence score
and verbatim quotes from the customer message. The parser turns those quotes into
character spans against the original message. Quotes are used instead of
model-reported offsets because the gateway redacts PII before the provider sees
the text, which can change offsets; a verbatim quote still identifies the source
unambiguously and is easy for a reviewer to check.

No field is saved without evidence. A field the model fills without a quote is a
silent assumption and raises ``SilentAssumptionError``; a quote that does not
occur in the message raises ``RequirementParseError``. Neither case is repaired
automatically: a model that invented support cannot be trusted to self-correct,
so a human reviews it.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Final, cast
from uuid import UUID

from pydantic import ValidationError
from sqlalchemy.orm import Session

from travelos.core.clock import Clock, SystemClock
from travelos.inquiry.errors import RequirementParseError, SilentAssumptionError
from travelos.inquiry.requirement import ParsedRequirement, Requirement, SourceSpan
from travelos.llm.gateway import LLMGateway
from travelos.llm.types import DataClass, LLMResult

PROMPT_NAME: Final = "inquiry_parser"
LANGUAGE_HINTS: Final[frozenset[str]] = frozenset({"en", "hi", "hinglish", "unknown"})

_OUTPUT_KEYS: Final[frozenset[str]] = frozenset(
    {"requirement", "confidence", "sources", "ambiguous_fields"}
)
_LIST_FIELDS: Final[frozenset[str]] = frozenset(
    {"destinations", "children_ages", "preferences", "special_needs"}
)


@dataclass(frozen=True, slots=True)
class ParseOutcome:
    """A parsed requirement plus the provenance needed for audit and eval."""

    parsed: ParsedRequirement
    prompt_name: str
    prompt_version: int
    provider: str
    model: str
    call_id: UUID | None


class InquiryParser:
    """Run the versioned parser prompt through the one LLM gateway."""

    def __init__(
        self,
        gateway: LLMGateway,
        *,
        prompt_name: str = PROMPT_NAME,
        version: int | None = None,
        clock: Clock | None = None,
    ) -> None:
        self._gateway = gateway
        self._prompt_name = prompt_name
        self._version = version
        self._clock = clock or SystemClock()

    def parse(
        self,
        db: Session,
        *,
        message: str,
        channel: str,
        language_hint: str = "unknown",
        tenant_id: UUID | None = None,
        actor_id: UUID | None = None,
    ) -> ParseOutcome:
        if not message.strip():
            raise RequirementParseError("cannot parse an empty message")
        if not channel.strip():
            raise RequirementParseError("channel is required")

        result = self._gateway.complete(
            db,
            prompt_name=self._prompt_name,
            version=self._version,
            variables={
                "message": message,
                "channel": channel.strip(),
                "language_hint": _language_hint(language_hint),
                "current_date": self._clock.now().date().isoformat(),
            },
            data_class=DataClass.CUSTOMER,
            tenant_id=tenant_id,
            actor_id=actor_id,
        )
        return _outcome(result, message)


def _outcome(result: LLMResult, message: str) -> ParseOutcome:
    parsed = parse_model_output(result.parsed, message)
    return ParseOutcome(
        parsed=parsed,
        prompt_name=result.prompt_name,
        prompt_version=result.prompt_version,
        provider=result.provider,
        model=result.model,
        call_id=result.call_id,
    )


def parse_model_output(payload: object, message: str) -> ParsedRequirement:
    """Validate one model payload against the schema, evidence and quote rules.

    Public so the eval can score recorded/fake payloads without a gateway call.
    """
    if payload is None:
        raise RequirementParseError("parser returned no structured output")
    document = _as_mapping(payload, "parser output")
    unknown = sorted(set(document) - _OUTPUT_KEYS)
    if unknown:
        raise RequirementParseError(f"unexpected parser output fields: {unknown}")

    requirement = _requirement(document.get("requirement"))
    confidence = _confidence(document.get("confidence"))
    sources = _sources(document.get("sources"))
    ambiguous = _ambiguous(document.get("ambiguous_fields"))
    spans = _resolve_sources(sources, message)

    try:
        parsed = ParsedRequirement(
            requirement=requirement,
            confidence=confidence,
            spans=spans,
            ambiguous_fields=ambiguous,
        )
    except ValidationError as exc:
        raise RequirementParseError(f"parser evidence failed validation: {exc}") from exc

    missing = parsed.silent_assumptions()
    if missing:
        raise SilentAssumptionError(
            "parser filled fields without a source quote: " + ", ".join(missing)
        )
    unconfident = tuple(field for field in spans if field not in confidence)
    if unconfident:
        raise RequirementParseError(
            "parser gave evidence without confidence: " + ", ".join(unconfident)
        )
    empty = tuple(field for field in spans if not _has_value(requirement, field))
    if empty:
        raise RequirementParseError(
            "parser gave source quotes for fields without a value: " + ", ".join(empty)
        )
    problems = parsed.span_problems(message)
    if problems:
        raise RequirementParseError("; ".join(problems))
    return parsed


def _requirement(value: object) -> Requirement:
    try:
        return Requirement.model_validate(value)
    except ValidationError as exc:
        raise RequirementParseError(f"requirement failed validation: {exc}") from exc


def _confidence(value: object) -> dict[str, float]:
    mapping = _as_mapping(value, "confidence")
    scores: dict[str, float] = {}
    for field, score in mapping.items():
        if not isinstance(field, str):
            raise RequirementParseError("confidence keys must be field names")
        if isinstance(score, bool) or not isinstance(score, int | float):
            raise RequirementParseError(f"confidence for {field} must be a number")
        scores[field] = float(score)
    return scores


def _sources(value: object) -> dict[str, tuple[str, ...]]:
    mapping = _as_mapping(value, "sources")
    result: dict[str, tuple[str, ...]] = {}
    for field, quotes in mapping.items():
        if not isinstance(field, str):
            raise RequirementParseError("source keys must be field names")
        result[field] = _quotes(quotes, field)
    return result


def _quotes(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise RequirementParseError(f"sources.{field} must be a list of quotes")
    quotes: list[str] = []
    for quote in value:
        if not isinstance(quote, str) or not quote.strip():
            raise RequirementParseError(f"sources.{field} contains an empty quote")
        quotes.append(quote)
    if not quotes:
        raise RequirementParseError(f"sources.{field} must contain at least one quote")
    return tuple(quotes)


def _ambiguous(value: object) -> tuple[str, ...]:
    if not isinstance(value, Sequence) or isinstance(value, str | bytes):
        raise RequirementParseError("ambiguous_fields must be a list")
    fields: list[str] = []
    for field in value:
        if not isinstance(field, str):
            raise RequirementParseError("ambiguous_fields must contain field names")
        fields.append(field)
    return tuple(fields)


def _resolve_sources(
    sources: Mapping[str, tuple[str, ...]], message: str
) -> dict[str, tuple[SourceSpan, ...]]:
    spans: dict[str, tuple[SourceSpan, ...]] = {}
    for field, quotes in sources.items():
        resolved: list[SourceSpan] = []
        for quote in quotes:
            span = locate_quote(quote, message)
            if span is None:
                raise RequirementParseError(
                    f"source quote for {field} does not occur in the message: {quote[:80]!r}"
                )
            resolved.append(span)
        spans[field] = tuple(resolved)
    return spans


def locate_quote(quote: str, message: str) -> SourceSpan | None:
    """Find a verbatim quote, tolerating case and whitespace differences."""

    text = quote.strip()
    if not text:
        return None
    index = message.find(text)
    if index >= 0:
        return SourceSpan(start=index, end=index + len(text))
    tokens = text.split()
    if not tokens:
        return None
    pattern = re.compile(r"\s+".join(re.escape(token) for token in tokens), re.IGNORECASE)
    match = pattern.search(message)
    if match is None:
        return None
    return SourceSpan(start=match.start(), end=match.end())


def _has_value(requirement: Requirement, field: str) -> bool:
    value = getattr(requirement, field)
    if field in _LIST_FIELDS:
        return bool(value)
    return value is not None


def _as_mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise RequirementParseError(f"{label} must be a JSON object")
    return cast(Mapping[str, object], value)


def _language_hint(value: str) -> str:
    hint = value.strip().lower()
    return hint if hint in LANGUAGE_HINTS else "unknown"
