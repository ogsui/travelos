"""Deterministic package component eval against a hand-checked gold set (task 013b1).

Each gold case is a requirement plus the normalized offers a search returned and
the components a human derived from the builder's documented rules. There is no
model here: the eval measures the deterministic builder, so a change to an
inclusion rule, an age boundary or a selection tie-break shows up as a component
mismatch.

The acceptance gate is a mean component-match rate of at least 85%, with every
rejected case reporting exactly the expected problem codes. The recorded
baseline is ``data/eval/results/package_components_v1.json``. Run::

    python -m travelos.packages.eval
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Final

from travelos.inquiry.requirement import Requirement
from travelos.packages.builder import build_package
from travelos.packages.models import (
    ComponentType,
    ProblemCode,
    UnitBasis,
)
from travelos.suppliers.models import CancellationRules, FareRules, Offer

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
DEFAULT_GOLD: Final = REPO_ROOT / "data" / "eval" / "package_gold.jsonl"
DEFAULT_RESULT: Final = REPO_ROOT / "data" / "eval" / "results" / "package_components_v1.json"

MODE: Final = "deterministic"
REQUIRED_COMPONENT_MATCH_RATE: Final = 0.85

# All fixtures share one clock so availability, expiry and hold deadlines are
# reproducible. A gold offer may move relative to it with fetched/expiry offsets.
GOLD_NOW: Final = datetime(2026, 11, 1, 10, 0, tzinfo=UTC)
_DEFAULT_EXPIRES_IN_MINUTES: Final = 10


@dataclass(frozen=True, slots=True)
class ExpectedComponent:
    component_type: ComponentType
    offer_id: str
    quantity: int
    unit_basis: UnitBasis


@dataclass(frozen=True, slots=True)
class GoldCase:
    id: str
    category: str
    requirement: Requirement
    offers: tuple[Offer, ...]
    expected_components: tuple[ExpectedComponent, ...]
    expected_problems: tuple[ProblemCode, ...]
    expected_total: int | None
    notes: str = ""


@dataclass(frozen=True, slots=True)
class CaseOutcome:
    case_id: str
    category: str
    matched_components: int
    component_ratio: float
    problems_match: bool
    cost_match: bool
    exact: bool
    expected_components: tuple[ExpectedComponent, ...]
    actual_components: tuple[ExpectedComponent, ...]
    expected_problems: tuple[ProblemCode, ...]
    actual_problems: tuple[ProblemCode, ...]
    expected_total: int | None
    actual_total: int | None


@dataclass(frozen=True, slots=True)
class PackageEvalResult:
    case_count: int
    component_match_rate: float
    exact_case_rate: float
    cost_match_rate: float
    problem_accuracy: float
    gate: Mapping[str, object]
    outcomes: tuple[Mapping[str, object], ...]
    failures: tuple[Mapping[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "mode": MODE,
            "case_count": self.case_count,
            "component_match_rate": round(self.component_match_rate, 4),
            "exact_case_rate": round(self.exact_case_rate, 4),
            "cost_match_rate": round(self.cost_match_rate, 4),
            "problem_accuracy": round(self.problem_accuracy, 4),
            "gate": dict(self.gate),
            "outcomes": [dict(outcome) for outcome in self.outcomes],
            "failures": [dict(failure) for failure in self.failures],
        }


def load_gold(path: Path = DEFAULT_GOLD) -> tuple[GoldCase, ...]:
    """Load the hand-checked cases; a malformed line names its file and line."""

    cases: list[GoldCase] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            raw: dict[str, Any] = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path.name} line {number}: invalid JSON") from exc
        cases.append(_case(raw))
    return tuple(cases)


def run_eval(
    cases: Sequence[GoldCase] | None = None,
    *,
    now: datetime = GOLD_NOW,
) -> PackageEvalResult:
    """Build every gold case and score component match, problems and totals."""

    selected = tuple(cases) if cases is not None else load_gold()
    if not selected:
        raise ValueError("the package gold set is empty")

    outcomes = tuple(_outcome(case, now=now) for case in selected)
    count = len(outcomes)
    component_match_rate = sum(outcome.component_ratio for outcome in outcomes) / count
    exact_case_rate = sum(1 for outcome in outcomes if outcome.exact) / count
    cost_match_rate = sum(1 for outcome in outcomes if outcome.cost_match) / count
    problem_accuracy = sum(1 for outcome in outcomes if outcome.problems_match) / count
    passed = component_match_rate >= REQUIRED_COMPONENT_MATCH_RATE and problem_accuracy == 1.0
    failures = tuple(
        _case_dict(outcome, include_expected=True) for outcome in outcomes if not outcome.exact
    )
    return PackageEvalResult(
        case_count=count,
        component_match_rate=component_match_rate,
        exact_case_rate=exact_case_rate,
        cost_match_rate=cost_match_rate,
        problem_accuracy=problem_accuracy,
        gate={
            "passed": passed,
            "required_component_match_rate": REQUIRED_COMPONENT_MATCH_RATE,
        },
        outcomes=tuple(_case_dict(outcome) for outcome in outcomes),
        failures=failures,
    )


def _case(raw: dict[str, Any]) -> GoldCase:
    expected = raw.get("expected")
    if not isinstance(expected, dict):
        raise ValueError(f"gold case {raw.get('id')!r} has no expected object")
    return GoldCase(
        id=str(raw["id"]),
        category=str(raw["category"]),
        requirement=Requirement.model_validate(raw["requirement"]),
        offers=tuple(_offer(offer) for offer in raw["offers"]),
        expected_components=tuple(
            _expected_component(item) for item in expected.get("components", [])
        ),
        expected_problems=tuple(ProblemCode(code) for code in expected.get("problems", [])),
        expected_total=expected.get("total_cost_paise"),
        notes=str(raw.get("notes", "")),
    )


def _expected_component(raw: Mapping[str, Any]) -> ExpectedComponent:
    return ExpectedComponent(
        component_type=ComponentType(str(raw["component_type"])),
        offer_id=str(raw["offer_id"]),
        quantity=int(raw["quantity"]),
        unit_basis=UnitBasis(str(raw["unit_basis"])),
    )


def _offer(raw: Mapping[str, Any]) -> Offer:
    currency = str(raw.get("currency", "INR"))
    fetched_at = GOLD_NOW + timedelta(minutes=int(raw.get("fetched_offset_minutes", 0)))
    expires_at = GOLD_NOW + timedelta(
        minutes=int(raw.get("expires_in_minutes", _DEFAULT_EXPIRES_IN_MINUTES))
    )
    return Offer(
        supplier=str(raw["supplier"]),
        offer_id=str(raw["offer_id"]),
        product_type=str(raw["product_type"]),
        name=str(raw["name"]),
        city=str(raw["city"]),
        route=raw.get("route"),
        currency=currency,
        net_paise=int(raw["net_paise"]),
        taxes_paise=int(raw.get("taxes_paise", 0)),
        available_count=int(raw.get("available_count", 9)),
        fare_rules=FareRules(
            cancellation=CancellationRules(
                refundable=True,
                cancellation_days=7,
                change_fee_paise=0,
                currency=currency,
            )
        ),
        fetched_at=fetched_at,
        expires_at=expires_at,
    )


def _outcome(case: GoldCase, *, now: datetime) -> CaseOutcome:
    build = build_package(case.requirement, case.offers, now=now)
    actual_components = (
        tuple(
            ExpectedComponent(
                component_type=component.component_type,
                offer_id=component.offer.offer_id,
                quantity=component.quantity,
                unit_basis=component.unit_basis,
            )
            for component in build.itinerary.components
        )
        if build.itinerary is not None
        else ()
    )
    actual_problems = tuple(problem.code for problem in build.problems)
    actual_total = build.itinerary.total_cost.paise if build.itinerary is not None else None
    problems_match = Counter(case.expected_problems) == Counter(actual_problems)

    if case.expected_components:
        matched = sum((Counter(case.expected_components) & Counter(actual_components)).values())
        denominator = max(len(case.expected_components), len(actual_components))
        ratio = matched / denominator
    else:
        matched = 0
        ratio = 1.0 if not actual_components and problems_match else 0.0

    cost_match = actual_total == case.expected_total
    exact = ratio == 1.0 and problems_match and cost_match
    return CaseOutcome(
        case_id=case.id,
        category=case.category,
        matched_components=matched,
        component_ratio=ratio,
        problems_match=problems_match,
        cost_match=cost_match,
        exact=exact,
        expected_components=case.expected_components,
        actual_components=actual_components,
        expected_problems=case.expected_problems,
        actual_problems=actual_problems,
        expected_total=case.expected_total,
        actual_total=actual_total,
    )


def _case_dict(outcome: CaseOutcome, *, include_expected: bool = False) -> dict[str, object]:
    data: dict[str, object] = {
        "case_id": outcome.case_id,
        "category": outcome.category,
        "matched_components": outcome.matched_components,
        "expected_component_count": len(outcome.expected_components),
        "component_ratio": round(outcome.component_ratio, 4),
        "problems_match": outcome.problems_match,
        "cost_match": outcome.cost_match,
        "exact": outcome.exact,
        "expected_problems": [problem.value for problem in outcome.expected_problems],
        "actual_problems": [problem.value for problem in outcome.actual_problems],
        "actual_total_paise": outcome.actual_total,
        "actual_components": [
            {
                "component_type": item.component_type.value,
                "offer_id": item.offer_id,
                "quantity": item.quantity,
                "unit_basis": item.unit_basis.value,
            }
            for item in outcome.actual_components
        ],
    }
    if include_expected:
        data["expected_total_paise"] = outcome.expected_total
        data["expected_components"] = [
            {
                "component_type": item.component_type.value,
                "offer_id": item.offer_id,
                "quantity": item.quantity,
                "unit_basis": item.unit_basis.value,
            }
            for item in outcome.expected_components
        ]
    return data


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--out", type=Path, default=DEFAULT_RESULT)
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    result = run_eval(load_gold(args.gold))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "case_count": result.case_count,
                "component_match_rate": round(result.component_match_rate, 4),
                "exact_case_rate": round(result.exact_case_rate, 4),
                "cost_match_rate": round(result.cost_match_rate, 4),
                "problem_accuracy": round(result.problem_accuracy, 4),
                "gate": dict(result.gate),
                "result": str(args.out),
            },
            indent=2,
            sort_keys=True,
        )
    )
    return 0 if result.gate["passed"] is True else 1


if __name__ == "__main__":  # pragma: no cover - CLI entry point
    raise SystemExit(main())
