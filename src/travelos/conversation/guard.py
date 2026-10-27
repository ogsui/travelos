"""Deterministic claim guard for customer-facing sales text (task 011a).

A reply may not state a price, availability, policy, discount or hard promise
unless the caller supplies a source for that claim kind. The guard is plain code
so the rule is reviewable and testable; it does not try to understand the text
and deliberately over-blocks rather than risk an unsupported promise.

Sources are opaque references to approved evidence (a quote revision, inventory
snapshot or approved knowledge-base entry) supplied by the caller. This slice has
no quote or KB store yet, so the only safe reply is one with no claims.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Final


class ClaimKind(StrEnum):
    """The claim categories the agency will not let a model or template invent."""

    MONEY = "money"
    AVAILABILITY = "availability"
    POLICY = "policy"
    DISCOUNT = "discount"
    PROMISE = "promise"


_PATTERNS: Final[dict[ClaimKind, tuple[re.Pattern[str], ...]]] = {
    ClaimKind.MONEY: (
        re.compile(r"(?:₹|rs\.?|inr)\s*\d[\d,]*(?:\.\d{1,2})?", re.IGNORECASE),
        re.compile(
            r"\b\d[\d,]*(?:\.\d{1,2})?\s*(?:rupees|rupaye|rupaya|rupay|inr)\b",
            re.IGNORECASE,
        ),
        re.compile(
            r"\b\d[\d,]*(?:\.\d{1,2})?\s*(?:lakh|lakhs|lac|lacs|crore|crores|cr)\b",
            re.IGNORECASE,
        ),
        re.compile(r"\b\d[\d,]*(?:\.\d{1,2})?\s*k\b", re.IGNORECASE),
        re.compile(
            r"\b(?:price|prices|cost|costs|fare|fares|total|amount|refund\s+amount)\b",
            re.IGNORECASE,
        ),
    ),
    ClaimKind.AVAILABILITY: (
        re.compile(
            r"\b(?:available|availability|sold\s*out|seats?\s+left|on\s+hold|"
            r"blocked|confirmed|confirmation|guaranteed)\b",
            re.IGNORECASE,
        ),
    ),
    ClaimKind.POLICY: (
        re.compile(
            r"\b(?:refund|refunds|refundable|cancell?ation|cancell?ations|visa|visas|"
            r"insurance|terms|policy|policies|penalt(?:y|ies)|amendment|amendments)\b",
            re.IGNORECASE,
        ),
    ),
    ClaimKind.DISCOUNT: (
        re.compile(
            r"\b(?:discount|discounted|cheaper|price\s+match|special\s+offer|"
            r"offer\s+price)\b|\d+\s*%",
            re.IGNORECASE,
        ),
    ),
    ClaimKind.PROMISE: (
        re.compile(
            r"\b(?:guarantee[ds]?|promise[ds]?|assure[ds]?|definitely|100\s*%)\b|"
            r"\bwithin\s+\d+\s*(?:minutes?|hours?|days?)\b|\bsame\s+day\b",
            re.IGNORECASE,
        ),
    ),
}

SourceMap = Mapping[str, Sequence[str]]


@dataclass(frozen=True, slots=True)
class Claim:
    """One detected claim with its character span in the draft body."""

    kind: ClaimKind
    text: str
    start: int
    end: int


@dataclass(frozen=True, slots=True)
class GuardReport:
    """Claims found in a draft and the sources supplied to support them."""

    claims: tuple[Claim, ...]
    sources: Mapping[str, tuple[str, ...]]

    @property
    def unsupported(self) -> tuple[Claim, ...]:
        return tuple(claim for claim in self.claims if not self.sources.get(claim.kind.value))

    @property
    def passed(self) -> bool:
        return not self.unsupported

    def kinds(self) -> tuple[str, ...]:
        return tuple(sorted({claim.kind.value for claim in self.claims}))

    def unsupported_kinds(self) -> tuple[str, ...]:
        return tuple(sorted({claim.kind.value for claim in self.unsupported}))


class UnsupportedClaimError(Exception):
    """A reply contains a claim with no approved source."""

    def __init__(self, report: GuardReport) -> None:
        self.report = report
        details = ", ".join(f"{claim.kind.value} ({claim.text!r})" for claim in report.unsupported)
        super().__init__(f"unsupported reply claim(s): {details}")


def _normalise_sources(sources: SourceMap | None) -> dict[str, tuple[str, ...]]:
    if sources is None:
        return {}
    normalised: dict[str, tuple[str, ...]] = {}
    for key, refs in sources.items():
        try:
            kind = ClaimKind(key)
        except ValueError as exc:
            raise ValueError(f"unknown claim source kind: {key!r}") from exc
        cleaned = tuple(ref.strip() for ref in refs)
        if not cleaned or any(not ref for ref in cleaned):
            raise ValueError(f"claim source {kind.value!r} needs at least one non-empty ref")
        normalised[kind.value] = cleaned
    return normalised


def scan_reply(body: str, sources: SourceMap | None = None) -> GuardReport:
    """Find every claim in ``body`` and mark which kinds have a source.

    The scan is intentionally conservative: a false positive blocks a draft for
    a human to edit, while a false negative could send an unsupported promise.
    """

    if not body.strip():
        raise ValueError("cannot scan an empty reply")
    found: dict[tuple[str, int, int], Claim] = {}
    for kind, patterns in _PATTERNS.items():
        for pattern in patterns:
            for match in pattern.finditer(body):
                text = match.group(0).strip()
                if not text:
                    continue
                found[(kind.value, match.start(), match.end())] = Claim(
                    kind=kind,
                    text=text,
                    start=match.start(),
                    end=match.end(),
                )
    claims = tuple(sorted(found.values(), key=lambda item: (item.start, item.end, item.kind)))
    return GuardReport(claims=claims, sources=_normalise_sources(sources))


def require_supported(body: str, sources: SourceMap | None = None) -> GuardReport:
    """Return a passed report or raise before anything is drafted or approved."""

    report = scan_reply(body, sources)
    if not report.passed:
        raise UnsupportedClaimError(report)
    return report
