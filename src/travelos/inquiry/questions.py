"""Deterministic wording for clarifying questions (task 010c).

No model writes these questions. Each gap code maps to one fixed sentence, so the
same gap always produces the same customer-facing text and a reviewer can approve
it without wondering what the model meant. The draft is still approval-gated: a
human sends it, and nothing here invents a destination, date or budget.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Final

from travelos.inquiry.requirement import REQUIREMENT_FIELDS
from travelos.inquiry.rules import EXTRA_QUESTION_CODES, MissingField

_QUESTIONS: Final[dict[str, str]] = {
    "trip_type": (
        "Could you confirm what you need — flights, a holiday package, a hotel, "
        "visa help, or something else?"
    ),
    "origin_city": "Which city will you be travelling from?",
    "destinations": "Where would you like to go?",
    "start_date": "What date would you like to start?",
    "end_date": "What date would you like to return or check out?",
    "date_flexibility_days": ("How many days either side of your preferred dates could you move?"),
    "adults": "How many adults (18 and over) are travelling?",
    "children_ages": "How many children are travelling, and what are their ages?",
    "infants": "How many infants under 2 are travelling?",
    "rooms": "How many rooms do you need?",
    "budget_paise": "Do you have a budget range in mind?",
    "budget_currency": "Which currency is your budget in?",
    "preferences": "Do you have any travel preferences we should know about?",
    "nationality": "What nationality is the passport you will travel on?",
    "special_needs": "Does anyone in the group have special needs we should plan for?",
    "urgency": "When do you need the quote by?",
    "infant_age": "How old is the infant?",
    "confirmed_dates": ("Are your travel dates confirmed, and how flexible are they?"),
    "traveller_documents": (
        "Do all travellers already have valid passports and any existing visas?"
    ),
}

_AMBIGUOUS_PREFIX: Final = "Just to confirm — "

_KNOWN: Final[frozenset[str]] = frozenset(REQUIREMENT_FIELDS) | frozenset(EXTRA_QUESTION_CODES)


def question_for(code: str, *, reason: str = "missing") -> str:
    """One question sentence for a gap code, or a safe generic fallback."""

    question = _QUESTIONS.get(code)
    if question is None:
        raise ValueError(f"unknown question code: {code!r}")
    if reason not in {"missing", "ambiguous"}:
        raise ValueError(f"unknown gap reason: {reason!r}")
    if reason == "ambiguous":
        return _AMBIGUOUS_PREFIX + question[0].lower() + question[1:]
    return question


def question_codes(missing: Sequence[MissingField]) -> tuple[str, ...]:
    """Stable, de-duplicated question codes in the order the gaps were found."""

    codes: list[str] = []
    for item in missing:
        if item.field not in codes:
            codes.append(item.field)
    return tuple(codes)


def draft_body(missing: Sequence[MissingField]) -> str:
    """A short numbered message built only from the fixed questions."""

    if not missing:
        raise ValueError("cannot draft a clarifying message with no gaps")
    lines = [
        "Namaste! Thank you for your travel enquiry. "
        "To prepare the right options for you, could you please share:",
    ]
    seen: set[str] = set()
    number = 0
    for item in missing:
        if item.field in seen:
            continue
        seen.add(item.field)
        number += 1
        lines.append(f"{number}. {question_for(item.field, reason=item.reason)}")
    lines.append("Please reply with these details and we will put together your options.")
    return "\n".join(lines)


def unknown_codes(codes: Iterable[str]) -> tuple[str, ...]:
    """Codes that would have no reviewed wording (for tests and guards)."""

    return tuple(sorted({code for code in codes if code not in _KNOWN}))
