"""Deterministic handoff triggers for sales conversations (task 011b).

The owner has not yet supplied a complaint/anger taxonomy, a loop threshold or
a response-time policy, so every rule here is deliberately conservative and
visible in one place. The owner did decide that *every* booking value goes to a
human, so the value floor is ``0``: any positive amount hands off.

Nothing in this module calls an LLM. A false positive sends a conversation to a
person, which is safe; a false negative could leave a complaint, refund request
or legal threat unanswered. The detector therefore over-blocks.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Final

from travelos.core.money import Money

_MIN_LOOP_REPEATS: Final = 3
_MAX_EVIDENCE_AMOUNTS: Final = 4


class HandoffTrigger(StrEnum):
    """Why a conversation needs a human. Values are stored, so keep them stable."""

    ANGER = "anger"
    COMPLAINT = "complaint"
    BOOKING_VALUE = "booking_value"
    LEGAL = "legal"
    VISA = "visa"
    REFUND = "refund"
    LOOP = "loop"


TRIGGER_LABELS: Final[dict[HandoffTrigger, str]] = {
    HandoffTrigger.ANGER: "customer upset",
    HandoffTrigger.COMPLAINT: "complaint",
    HandoffTrigger.BOOKING_VALUE: "booking value",
    HandoffTrigger.LEGAL: "legal",
    HandoffTrigger.VISA: "visa",
    HandoffTrigger.REFUND: "refund/cancellation",
    HandoffTrigger.LOOP: "repeated question",
}

# Phrases are matched case-insensitively on word boundaries. Hinglish spellings
# are included because that is how many customers actually write.
_PHRASES: Final[dict[HandoffTrigger, tuple[str, ...]]] = {
    HandoffTrigger.ANGER: (
        "angry",
        "anger",
        "furious",
        "mad",
        "annoyed",
        "frustrated",
        "frustrating",
        "upset",
        "disappointed",
        "disappointing",
        "terrible",
        "horrible",
        "awful",
        "worst",
        "pathetic",
        "ridiculous",
        "unacceptable",
        "useless",
        "fed up",
        "sick of",
        "had enough",
        "nonsense",
        "shocking",
        "disgusted",
        "bekaar",
        "bakwas",
        "bakwaas",
        "ghatiya",
        "faltu",
        "pagal",
        "dhoka",
        "dhokha",
        "loot",
        "looted",
        "naaraz",
        "naraz",
        "pareshan",
    ),
    HandoffTrigger.COMPLAINT: (
        "complain",
        "complains",
        "complained",
        "complaint",
        "complaints",
        "grievance",
        "escalate",
        "escalation",
        "supervisor",
        "manager",
        "customer care",
        "poor service",
        "bad service",
        "bad experience",
        "not happy",
        "unhappy",
        "dissatisfied",
        "not satisfied",
        "overcharged",
        "overcharge",
        "wrong charge",
        "no response",
        "not responding",
        "did not respond",
        "shikayat",
        "compensation",
    ),
    HandoffTrigger.LEGAL: (
        "lawyer",
        "legal",
        "legal action",
        "legal notice",
        "legal advice",
        "legal proceedings",
        "consumer court",
        "consumer forum",
        "court",
        "sue",
        "lawsuit",
        "litigation",
        "advocate",
        "solicitor",
        "police",
        "fir",
        "ombudsman",
        "dgca",
        "nclt",
        "arbitration",
    ),
    HandoffTrigger.VISA: (
        "visa",
        "visas",
        "e-visa",
        "evisa",
        "passport",
        "passports",
        "immigration",
        "embassy",
        "consulate",
        "schengen",
        "oci",
        "pio card",
        "travel document",
        "travel documents",
        "vfs",
    ),
    HandoffTrigger.REFUND: (
        "refund",
        "refunds",
        "refundable",
        "refund amount",
        "money back",
        "chargeback",
        "cancel",
        "cancels",
        "cancelled",
        "canceled",
        "cancellation",
        "cancellations",
        "amend",
        "amendment",
        "amendments",
        "reschedule",
        "rescheduling",
        "date change",
        "change the date",
        "change my booking",
        "modify my booking",
        "postpone",
        "postponement",
    ),
}

_COMPILED: Final[dict[HandoffTrigger, tuple[tuple[str, re.Pattern[str]], ...]]] = {
    trigger: tuple(
        (phrase, re.compile(rf"\b{re.escape(phrase)}\b", re.IGNORECASE)) for phrase in phrases
    )
    for trigger, phrases in _PHRASES.items()
}

_QUESTION_WORDS: Final = re.compile(
    r"\b(?:what|when|where|which|who|whom|whose|why|how|can|could|will|would|should|"
    r"is|are|do|does|did|kya|kaise|kab|kahan|kitna|kitni|kaun|kyun)\b",
    re.IGNORECASE,
)

# Currency forms: "₹45,000", "Rs. 45000", "INR 75,000", "45000 rupees", "1.5 lakh".
# Bare numbers are not money: "2 adults, 3 nights" must never trigger a handoff.
_AMOUNT_PATTERNS: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(
        r"(?:₹\s*|(?:\brs\.?|\binr)\s*)\d[\d,]*(?:\.\d{1,2})?"
        r"(?:\s*(?:k|thousand|lakh|lac|lakhs|crore|cr|crores))?",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b\d[\d,]*(?:\.\d{1,2})?\s*(?:k|thousand|lakh|lac|lakhs|crore|cr|crores)?"
        r"\s*\b(?:rupees|inr)\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b\d[\d,]*(?:\.\d{1,2})?\s*(?:lakh|lac|lakhs|crore|cr|crores)\b",
        re.IGNORECASE,
    ),
)

_HIGH_PRIORITY: Final[frozenset[HandoffTrigger]] = frozenset(
    {
        HandoffTrigger.ANGER,
        HandoffTrigger.COMPLAINT,
        HandoffTrigger.LEGAL,
        HandoffTrigger.REFUND,
    }
)
_OWNER_ASSIGNED: Final[frozenset[HandoffTrigger]] = frozenset(
    {
        HandoffTrigger.BOOKING_VALUE,
        HandoffTrigger.LEGAL,
        HandoffTrigger.REFUND,
    }
)


@dataclass(frozen=True, slots=True)
class TriggerEvidence:
    """One detected reason plus a short, reviewable explanation."""

    trigger: HandoffTrigger
    detail: str


@dataclass(frozen=True, slots=True)
class HandoffPolicy:
    """Owner-adjustable thresholds. Defaults are conservative placeholders.

    ``min_value_paise=0`` means "every positive amount": the owner explicitly
    chose that every booking value goes to a human. A positive floor requires a
    value at or above it. ``loop_repeat_threshold`` is at least three because
    two identical questions are not a loop.
    """

    min_value_paise: int = 0
    include_stated_budget: bool = True
    loop_repeat_threshold: int = _MIN_LOOP_REPEATS
    loop_window: timedelta = timedelta(days=7)

    def __post_init__(self) -> None:
        if isinstance(self.min_value_paise, bool) or self.min_value_paise < 0:
            raise ValueError("min_value_paise must be a non-negative integer")
        if self.loop_repeat_threshold < _MIN_LOOP_REPEATS:
            raise ValueError(f"a loop needs at least {_MIN_LOOP_REPEATS} repeats")
        if self.loop_window <= timedelta(0):
            raise ValueError("the loop window must be positive")

    def priority(self, triggers: Iterable[HandoffTrigger]) -> str:
        found = set(triggers)
        return "high" if found & _HIGH_PRIORITY else "normal"

    def assignee_role(self, triggers: Iterable[HandoffTrigger]) -> str:
        found = set(triggers)
        return "owner" if found & _OWNER_ASSIGNED else "agent"


def _validate_paise(value: int | None, label: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{label} must be a non-negative integer number of paise")
    return value


def _passes_floor(value: int, minimum: int) -> bool:
    return value > 0 and value >= minimum


def detect_keyword_triggers(texts: Sequence[str]) -> tuple[TriggerEvidence, ...]:
    """Find anger, complaint, legal, visa and refund/cancellation language."""

    found: list[TriggerEvidence] = []
    for trigger in (
        HandoffTrigger.ANGER,
        HandoffTrigger.COMPLAINT,
        HandoffTrigger.LEGAL,
        HandoffTrigger.VISA,
        HandoffTrigger.REFUND,
    ):
        matches: list[str] = []
        for phrase, pattern in _COMPILED[trigger]:
            if any(pattern.search(text) for text in texts):
                matches.append(phrase)
        if matches:
            shown = ", ".join(repr(phrase) for phrase in matches[:3])
            if len(matches) > 3:
                shown += f" (+{len(matches) - 3} more)"
            found.append(TriggerEvidence(trigger, f"matched {shown}"))
    return tuple(found)


def detect_amount_triggers(
    texts: Sequence[str],
    *,
    booking_value_paise: int | None = None,
    stated_budget_paise: int | None = None,
    min_value_paise: int = 0,
) -> tuple[TriggerEvidence, ...]:
    """Detect any monetary commitment, including a customer-stated budget."""

    booking_value = _validate_paise(booking_value_paise, "booking value")
    stated_budget = _validate_paise(stated_budget_paise, "stated budget")
    floor = _validate_paise(min_value_paise, "min_value_paise")
    assert floor is not None

    details: list[str] = []
    if booking_value is not None and _passes_floor(booking_value, floor):
        details.append(f"booking value {Money(booking_value).format()}")
    if stated_budget is not None and _passes_floor(stated_budget, floor):
        details.append(f"stated budget {Money(stated_budget).format()}")

    seen: set[str] = set()
    for text in texts:
        for pattern in _AMOUNT_PATTERNS:
            for match in pattern.finditer(text):
                amount = " ".join(match.group(0).split())
                key = amount.lower()
                if not amount or key in seen:
                    continue
                seen.add(key)
                details.append(f"amount mentioned {amount!r}")
                if len(details) >= _MAX_EVIDENCE_AMOUNTS:
                    break
            if len(details) >= _MAX_EVIDENCE_AMOUNTS:
                break
        if len(details) >= _MAX_EVIDENCE_AMOUNTS:
            break

    if not details:
        return ()
    return (TriggerEvidence(HandoffTrigger.BOOKING_VALUE, "; ".join(details)),)


def _is_question(text: str) -> bool:
    return "?" in text or bool(_QUESTION_WORDS.search(text))


def _normalise_question(text: str) -> str:
    return " ".join(re.sub(r"[^\w\s]", " ", text.casefold()).split())


def detect_loop_triggers(
    messages: Sequence[tuple[datetime, str]],
    *,
    now: datetime,
    policy: HandoffPolicy | None = None,
) -> tuple[TriggerEvidence, ...]:
    """Detect the same question repeated inside the policy window.

    Only exact repeats of a question count. That is deliberately boring: a
    staff member can reproduce the decision from the message history.
    """

    resolved = policy or HandoffPolicy()
    window_start = now - resolved.loop_window
    counts: dict[str, int] = {}
    for occurred_at, body in messages:
        if occurred_at.tzinfo is None:
            raise ValueError("message timestamps must be timezone-aware")
        if occurred_at < window_start or occurred_at > now:
            continue
        if not _is_question(body):
            continue
        key = _normalise_question(body)
        if not key:
            continue
        counts[key] = counts.get(key, 0) + 1

    repeated = max(counts.values(), default=0)
    if repeated < resolved.loop_repeat_threshold:
        return ()
    days = int(resolved.loop_window.total_seconds() // 86_400)
    window = (
        f"{days} days" if days > 0 else f"{int(resolved.loop_window.total_seconds() // 60)} minutes"
    )
    return (
        TriggerEvidence(
            HandoffTrigger.LOOP,
            f"same question repeated {repeated} times within {window}",
        ),
    )


def _ordered(evidence: Sequence[TriggerEvidence]) -> tuple[TriggerEvidence, ...]:
    by_trigger: dict[HandoffTrigger, TriggerEvidence] = {}
    for item in evidence:
        by_trigger.setdefault(item.trigger, item)
    ordered: list[TriggerEvidence] = []
    for trigger in HandoffTrigger:
        candidate = by_trigger.get(trigger)
        if candidate is not None:
            ordered.append(candidate)
    return tuple(ordered)


def detect_triggers(
    *,
    texts: Sequence[str] = (),
    loop_messages: Sequence[tuple[datetime, str]] = (),
    now: datetime,
    booking_value_paise: int | None = None,
    stated_budget_paise: int | None = None,
    policy: HandoffPolicy | None = None,
) -> tuple[TriggerEvidence, ...]:
    """Return every trigger in a stable order, one entry per reason."""

    resolved = policy or HandoffPolicy()
    evidence = list(detect_keyword_triggers(texts))
    evidence.extend(
        detect_amount_triggers(
            texts,
            booking_value_paise=booking_value_paise,
            stated_budget_paise=stated_budget_paise,
            min_value_paise=resolved.min_value_paise,
        )
    )
    evidence.extend(detect_loop_triggers(loop_messages, now=now, policy=resolved))
    return _ordered(evidence)
