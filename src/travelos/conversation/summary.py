"""Deterministic conversation summaries for human handoffs (task 011b).

A summary is assembled from structured rows (inquiry, latest requirement,
message timeline) and trigger evidence. No model writes it, so it cannot invent
a destination, price or promise, and the same inputs always produce the same
text. It is stored encrypted because it quotes customer messages and names.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final

from travelos.conversation.triggers import TRIGGER_LABELS, TriggerEvidence
from travelos.inquiry.requirement import Requirement

SUMMARY_VERSION: Final = 1
MAX_SUMMARY_CHARS: Final = 4000
_MAX_EXCERPT_CHARS: Final = 280


@dataclass(frozen=True, slots=True)
class ConversationFacts:
    """Everything the summary needs, already read from the database."""

    inquiry_state: str
    trip_type: str
    urgency: str
    customer_name: str | None
    channel: str
    language: str | None
    requirement: Requirement | None
    stated_fields: tuple[str, ...]
    missing_fields: tuple[str, ...]
    inbound_count: int
    outbound_count: int
    last_inbound_at: datetime | None
    last_outbound_at: datetime | None
    last_inbound: str | None
    last_outbound: str | None
    pending_drafts: int
    generated_at: datetime


def _utc(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")


def _count(value: int, singular: str, plural: str | None = None) -> str:
    return f"{value} {singular if value == 1 else (plural or singular + 's')}"


def _travellers(requirement: Requirement) -> str:
    parts: list[str] = []
    if requirement.adults is not None:
        parts.append(_count(requirement.adults, "adult"))
    if requirement.children_ages:
        ages = ", ".join(str(age) for age in requirement.children_ages)
        if len(requirement.children_ages) == 1:
            parts.append(f"1 child (age {ages})")
        else:
            parts.append(f"{len(requirement.children_ages)} children (ages {ages})")
    if requirement.infants:
        parts.append(_count(requirement.infants, "infant"))
    if requirement.rooms is not None:
        parts.append(_count(requirement.rooms, "room"))
    return ", ".join(parts) if parts else "travellers not stated"


def _dates(requirement: Requirement) -> str:
    start = requirement.start_date.isoformat() if requirement.start_date else None
    end = requirement.end_date.isoformat() if requirement.end_date else None
    if start and end:
        return f"{start} to {end}"
    if start:
        return f"from {start} (end not stated)"
    if end:
        return f"until {end} (start not stated)"
    return "dates not stated"


def _route(requirement: Requirement) -> str:
    destinations = ", ".join(requirement.destinations) if requirement.destinations else "not stated"
    if requirement.origin_city:
        return f"from {requirement.origin_city} to {destinations}"
    return f"to {destinations}"


def _requirement_line(requirement: Requirement | None) -> str:
    if requirement is None:
        return "Requirement: not parsed yet"
    budget = requirement.budget.format() if requirement.budget is not None else "not stated"
    return (
        f"Requirement: {requirement.trip_type} · {_travellers(requirement)} · "
        f"{_route(requirement)} · {_dates(requirement)} · budget {budget}"
    )


def _excerpt(text: str | None) -> str:
    if not text:
        return "none on record"
    clean = " ".join(text.split())
    if len(clean) <= _MAX_EXCERPT_CHARS:
        return f'"{clean}"'
    return f'"{clean[: _MAX_EXCERPT_CHARS - 3].rstrip()}..."'


def _truncate(text: str) -> str:
    if len(text) <= MAX_SUMMARY_CHARS:
        return text
    suffix = "\n[summary truncated]"
    return text[: MAX_SUMMARY_CHARS - len(suffix)].rstrip() + suffix


def build_summary(facts: ConversationFacts, triggers: Sequence[TriggerEvidence]) -> str:
    """Build the staff-facing handoff summary."""

    lines: list[str] = [
        f"Conversation handoff summary (v{SUMMARY_VERSION})",
        f"Generated: {_utc(facts.generated_at)}",
    ]

    # Triggers go first so truncation can never hide why the handoff exists.
    if triggers:
        lines.append("Triggers:")
        for item in triggers:
            lines.append(f"- {TRIGGER_LABELS[item.trigger]}: {item.detail}")
    else:
        lines.append("Triggers: none")

    lines.extend(
        [
            f"Inquiry: {facts.inquiry_state} · {facts.trip_type} · urgency {facts.urgency}",
            f"Customer: {facts.customer_name or 'not linked'}",
            (
                f"Channel: {facts.channel} · language {facts.language}"
                if facts.language
                else f"Channel: {facts.channel}"
            ),
            _requirement_line(facts.requirement),
        ]
    )

    if facts.stated_fields:
        lines.append(f"Stated: {', '.join(facts.stated_fields)}")
    if facts.missing_fields:
        lines.append(f"Missing: {', '.join(facts.missing_fields)}")
    if facts.requirement is not None:
        if facts.requirement.preferences:
            lines.append(f"Preferences: {', '.join(facts.requirement.preferences)}")
        if facts.requirement.special_needs:
            lines.append(f"Special needs: {', '.join(facts.requirement.special_needs)}")

    last_customer = _utc(facts.last_inbound_at) if facts.last_inbound_at is not None else "none"
    lines.extend(
        [
            (
                f"Messages: {facts.inbound_count} inbound, "
                f"{facts.outbound_count} outbound · last customer message {last_customer}"
            ),
            f"Last customer message: {_excerpt(facts.last_inbound)}",
            f"Last outbound message: {_excerpt(facts.last_outbound)}",
            f"Drafts waiting for approval: {facts.pending_drafts}",
        ]
    )
    return _truncate("\n".join(lines))
