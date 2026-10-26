"""Hand-checked inquiry-parser eval and result writer (task 010d).

Two modes:

* ``fake`` echoes the gold answer back through the real schema, evidence and
  scoring code. It proves the eval plumbing in CI, but it is *not* a model
  accuracy measurement; its result file says so.
* ``provider`` calls the owner-configured model through the LLM gateway. This is
  the only mode that can pass the accuracy gate and produce a baseline.

Run from the repository root::

    python -m travelos.inquiry.eval --mode fake
    python -m travelos.inquiry.eval --mode provider --database-url ...

The result is written to ``data/eval/results/<prompt>_v<version>.json``. A fake
result never overwrites a provider result.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Final, cast
from uuid import UUID

from sqlalchemy.orm import Session

from travelos.core.config import get_settings
from travelos.core.db import DEFAULT_TENANT_ID, build_engine, build_session_factory
from travelos.inquiry.errors import RequirementParseError, SilentAssumptionError
from travelos.inquiry.parser import (
    InquiryParser,
    locate_quote,
    parse_model_output,
)
from travelos.inquiry.requirement import REQUIREMENT_FIELDS, ParsedRequirement, Requirement
from travelos.inquiry.rules import blocking_missing_fields, required_fields
from travelos.llm.errors import LLMError
from travelos.llm.factory import build_gateway
from travelos.llm.prompts import PromptRegistry

PROMPT_NAME: Final = "inquiry_parser"
MIN_REQUIRED_ACCURACY: Final = 0.95
_BUCKETS: Final = ((0.0, 0.5), (0.5, 0.7), (0.7, 0.9), (0.9, 1.0001))


@dataclass(frozen=True, slots=True)
class GoldCase:
    """One synthetic message with a hand-checked requirement and its evidence."""

    id: str
    category: str
    language: str
    channel: str
    message: str
    requirement: Requirement
    evidence: Mapping[str, tuple[str, ...]]
    ambiguous_fields: tuple[str, ...] = ()
    notes: str = ""


@dataclass(frozen=True, slots=True)
class InjectionCase:
    """A compromised model output that the parser must reject."""

    id: str
    message: str
    payload: object
    expected: str


@dataclass(frozen=True, slots=True)
class Prediction:
    """One parser result; ``parsed`` is ``None`` when the call failed."""

    parsed: ParsedRequirement | None
    error: str | None = None
    silently_assumed: bool = False
    provider: str | None = None
    model: str | None = None


@dataclass(slots=True)
class _FieldTally:
    expected: int = 0
    matched: int = 0
    missed: int = 0
    wrong: int = 0
    extra: int = 0

    @property
    def accuracy(self) -> float:
        return self.matched / self.expected if self.expected else 1.0


@dataclass(frozen=True, slots=True)
class EvalResult:
    prompt_name: str
    prompt_version: int
    mode: str
    provider: str | None
    model: str | None
    generated_at: datetime
    case_count: int
    required_field_accuracy: float
    extraction_accuracy: float
    precision: float
    recall: float
    missing_info_accuracy: float
    parse_failures: int
    silent_assumptions: int
    silent_assumption_cases: tuple[str, ...]
    per_field: Mapping[str, Mapping[str, int | float]]
    calibration: Mapping[str, object]
    injection: Mapping[str, object]
    gate: Mapping[str, object]
    failures: tuple[Mapping[str, object], ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "prompt_name": self.prompt_name,
            "prompt_version": self.prompt_version,
            "mode": self.mode,
            "provider": self.provider,
            "model": self.model,
            "generated_at": self.generated_at.isoformat(),
            "case_count": self.case_count,
            "required_field_accuracy": round(self.required_field_accuracy, 4),
            "extraction_accuracy": round(self.extraction_accuracy, 4),
            "precision": round(self.precision, 4),
            "recall": round(self.recall, 4),
            "missing_info_accuracy": round(self.missing_info_accuracy, 4),
            "parse_failures": self.parse_failures,
            "silent_assumptions": self.silent_assumptions,
            "silent_assumption_cases": list(self.silent_assumption_cases),
            "per_field": {name: dict(values) for name, values in self.per_field.items()},
            "calibration": dict(self.calibration),
            "injection": dict(self.injection),
            "gate": dict(self.gate),
            "failures": [dict(item) for item in self.failures],
        }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise ValueError(f"{label} must be a JSON object")
    return cast(Mapping[str, object], value)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{label} must be a non-empty string")
    return value


def _text_list(value: object, label: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ValueError(f"{label} must be a list of strings")
    return tuple(cast(list[str], value))


def load_gold(path: Path) -> list[GoldCase]:
    cases: list[GoldCase] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: invalid JSON: {exc}") from exc
        document = _object(raw, f"{path}:{number}")
        evidence = {
            field: _text_list(quotes, f"{path}:{number} evidence.{field}")
            for field, quotes in _object(
                document.get("evidence", {}), f"{path}:{number} evidence"
            ).items()
        }
        cases.append(
            GoldCase(
                id=_text(document.get("id"), f"{path}:{number} id"),
                category=_text(document.get("category"), f"{path}:{number} category"),
                language=_text(document.get("language"), f"{path}:{number} language"),
                channel=_text(document.get("channel"), f"{path}:{number} channel"),
                message=_text(document.get("message"), f"{path}:{number} message"),
                requirement=Requirement.model_validate(document.get("gold_requirement")),
                evidence=evidence,
                ambiguous_fields=_text_list(
                    document.get("ambiguous_fields", []), f"{path}:{number} ambiguous_fields"
                ),
                notes=str(document.get("notes") or ""),
            )
        )
    if not cases:
        raise ValueError(f"{path} contains no gold cases")
    return cases


def validate_gold_case(case: GoldCase) -> None:
    """Every asserted gold value must have a verbatim quote in the message."""

    asserted = set(case.requirement.asserted_fields())
    if set(case.evidence) != asserted:
        raise ValueError(
            f"{case.id}: evidence fields {sorted(case.evidence)} do not match "
            f"asserted fields {sorted(asserted)}"
        )
    for field, quotes in case.evidence.items():
        if not quotes:
            raise ValueError(f"{case.id}: evidence for {field} is empty")
        for quote in quotes:
            if locate_quote(quote, case.message) is None:
                raise ValueError(f"{case.id}: quote {quote!r} for {field} is not in the message")
    unknown = sorted(set(case.ambiguous_fields) - set(REQUIREMENT_FIELDS))
    if unknown:
        raise ValueError(f"{case.id}: ambiguous fields are not known: {unknown}")


def load_injection(path: Path) -> list[InjectionCase]:
    cases: list[InjectionCase] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        document = _object(json.loads(line), f"{path}:{number}")
        cases.append(
            InjectionCase(
                id=_text(document.get("id"), f"{path}:{number} id"),
                message=_text(document.get("message"), f"{path}:{number} message"),
                payload=document.get("payload"),
                expected=_text(document.get("expected"), f"{path}:{number} expected"),
            )
        )
    if not cases:
        raise ValueError(f"{path} contains no injection cases")
    return cases


# ---------------------------------------------------------------------------
# Scoring
# ---------------------------------------------------------------------------


def _present(value: object) -> bool:
    if isinstance(value, list):
        return bool(value)
    return value is not None


def _normalised(value: object) -> object:
    if isinstance(value, list):
        return tuple(sorted(str(item) for item in value))
    if isinstance(value, date):
        return value.isoformat()
    return value


def _matches(expected: object, actual: object) -> bool:
    return _normalised(expected) == _normalised(actual)


def _json_value(value: object) -> object:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, list):
        return [_json_value(item) for item in value]
    return value


def _field_tallies() -> dict[str, _FieldTally]:
    names = {
        field
        for trip_type in ("flight", "package", "hotel", "visa", "corporate", "group", "unknown")
        for field in required_fields(trip_type)
    }
    return {name: _FieldTally() for name in sorted(names)}


def _calibration(pairs: Sequence[tuple[float, bool]]) -> dict[str, object]:
    if not pairs:
        return {"scored": 0, "brier": None, "buckets": []}
    brier = sum((confidence - float(correct)) ** 2 for confidence, correct in pairs) / len(pairs)
    buckets: list[dict[str, object]] = []
    for lower, upper in _BUCKETS:
        selected = [correct for confidence, correct in pairs if lower <= confidence < upper]
        buckets.append(
            {
                "min": lower,
                "max": min(upper, 1.0),
                "count": len(selected),
                "accuracy": round(sum(selected) / len(selected), 4) if selected else None,
            }
        )
    return {"scored": len(pairs), "brier": round(brier, 4), "buckets": buckets}


def run_injection_cases(cases: Sequence[InjectionCase]) -> dict[str, object]:
    """A parser that rejects every compromised payload has contained the injection."""

    contained = 0
    failures: list[dict[str, object]] = []
    for case in cases:
        try:
            parse_model_output(case.payload, case.message)
        except (RequirementParseError, SilentAssumptionError) as exc:
            if type(exc).__name__ == case.expected:
                contained += 1
            else:
                failures.append(
                    {
                        "id": case.id,
                        "expected": case.expected,
                        "got": type(exc).__name__,
                        "detail": str(exc)[:200],
                    }
                )
        except Exception as exc:  # pragma: no cover - a parser bug, not containment
            failures.append(
                {
                    "id": case.id,
                    "expected": case.expected,
                    "got": type(exc).__name__,
                    "detail": str(exc)[:200],
                }
            )
        else:
            failures.append({"id": case.id, "expected": case.expected, "got": "accepted"})
    return {
        "total": len(cases),
        "contained": contained,
        "failures": failures,
        "action_capable": False,
    }


def evaluate(
    cases: Sequence[GoldCase],
    predictions: Sequence[Prediction],
    *,
    mode: str,
    prompt_name: str,
    prompt_version: int,
    injection_cases: Sequence[InjectionCase],
    now: datetime | None = None,
) -> EvalResult:
    if len(cases) != len(predictions):
        raise ValueError("each gold case needs exactly one prediction")

    tallies = _field_tallies()
    calibration_pairs: list[tuple[float, bool]] = []
    failures: list[dict[str, object]] = []
    silent_cases: set[str] = set()
    missing_matches = 0
    parse_failures = 0
    required_correct = 0
    required_total = 0
    true_positive = 0
    false_negative = 0
    false_positive = 0
    asserted_total = 0
    asserted_correct = 0

    for case, prediction in zip(cases, predictions, strict=True):
        fields = required_fields(case.requirement.trip_type)
        asserted = case.requirement.asserted_fields()
        asserted_total += len(asserted)
        if prediction.parsed is None:
            parse_failures += 1
            if prediction.silently_assumed:
                silent_cases.add(case.id)
            failures.append(
                {
                    "id": case.id,
                    "reason": prediction.error or "parse_failed",
                }
            )
            for name in fields:
                tale = tallies[name]
                tale.expected += 1
                required_total += 1
                if _present(getattr(case.requirement, name)):
                    tale.missed += 1
                    false_negative += 1
                else:
                    # Nothing was invented, so an absent-but-expected-absent field
                    # is still a correct field-level answer.
                    required_correct += 1
            continue

        parsed = prediction.parsed
        if prediction.silently_assumed or parsed.silent_assumptions():
            silent_cases.add(case.id)

        for name in asserted:
            if _matches(getattr(case.requirement, name), getattr(parsed.requirement, name)):
                asserted_correct += 1

        for name in fields:
            expected = getattr(case.requirement, name)
            actual = getattr(parsed.requirement, name)
            gold_present = _present(expected)
            predicted_present = _present(actual)
            correct = _matches(expected, actual)
            tale = tallies[name]
            tale.expected += 1
            required_total += 1
            if correct:
                tale.matched += 1
                required_correct += 1
                if gold_present:
                    true_positive += 1
            elif gold_present:
                tale.missed += 1
                false_negative += 1
                if predicted_present:
                    # A wrong value is both a missed gold value and a false answer.
                    tale.wrong += 1
                    false_positive += 1
            elif predicted_present:
                tale.extra += 1
                false_positive += 1
            if not correct:
                failures.append(
                    {
                        "id": case.id,
                        "field": name,
                        "expected": _json_value(expected),
                        "actual": _json_value(actual),
                    }
                )

        expected_missing = {
            item.field
            for item in blocking_missing_fields(
                case.requirement, ambiguous_fields=case.ambiguous_fields
            )
        }
        actual_missing = {
            item.field
            for item in blocking_missing_fields(
                parsed.requirement, ambiguous_fields=parsed.ambiguous_fields
            )
        }
        if expected_missing == actual_missing:
            missing_matches += 1

        for name, confidence in parsed.confidence.items():
            actual = getattr(parsed.requirement, name)
            if not _present(actual):
                continue
            calibration_pairs.append(
                (confidence, _matches(getattr(case.requirement, name), actual))
            )

    required_accuracy = required_correct / required_total if required_total else 1.0
    extraction_accuracy = asserted_correct / asserted_total if asserted_total else 1.0
    answered = true_positive + false_positive
    precision = true_positive / answered if answered else 1.0
    recall = (
        true_positive / (true_positive + false_negative) if true_positive + false_negative else 1.0
    )
    injection = run_injection_cases(injection_cases)
    injection_ok = injection["contained"] == injection["total"]
    silent_count = len(silent_cases)

    if mode == "fake":
        gate: dict[str, object] = {
            "status": "not_measured",
            "passed": None,
            "required_field_accuracy_min": MIN_REQUIRED_ACCURACY,
            "reason": (
                "fake mode echoes the gold answer; it proves the eval plumbing but "
                "is not model accuracy. Run provider mode for a baseline."
            ),
        }
    else:
        reasons: list[str] = []
        if required_accuracy < MIN_REQUIRED_ACCURACY:
            reasons.append(
                f"required-field accuracy {required_accuracy:.4f} is below "
                f"{MIN_REQUIRED_ACCURACY:.2f}"
            )
        if silent_count:
            reasons.append(f"{silent_count} case(s) had a silent assumption")
        if parse_failures:
            reasons.append(f"{parse_failures} case(s) failed to parse")
        if not injection_ok:
            reasons.append(f"injection containment {injection['contained']}/{injection['total']}")
        gate = {
            "status": "passed" if not reasons else "failed",
            "passed": not reasons,
            "required_field_accuracy_min": MIN_REQUIRED_ACCURACY,
            "reason": "; ".join(reasons) or "all gates met",
        }

    providers = sorted({item.provider for item in predictions if item.provider})
    models = sorted({item.model for item in predictions if item.model})
    return EvalResult(
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        mode=mode,
        provider=", ".join(providers) or None,
        model=", ".join(models) or None,
        generated_at=now or datetime.now(UTC),
        case_count=len(cases),
        required_field_accuracy=required_accuracy,
        extraction_accuracy=extraction_accuracy,
        precision=precision,
        recall=recall,
        missing_info_accuracy=missing_matches / len(cases) if cases else 1.0,
        parse_failures=parse_failures,
        silent_assumptions=silent_count,
        silent_assumption_cases=tuple(sorted(silent_cases)),
        per_field={
            name: {
                "expected": tale.expected,
                "matched": tale.matched,
                "missed": tale.missed,
                "wrong": tale.wrong,
                "extra": tale.extra,
                "accuracy": round(tale.accuracy, 4),
            }
            for name, tale in tallies.items()
        },
        calibration=_calibration(calibration_pairs),
        injection=injection,
        gate=gate,
        failures=tuple(failures),
    )


# ---------------------------------------------------------------------------
# Modes
# ---------------------------------------------------------------------------


def _gold_echo(case: GoldCase) -> Prediction:
    asserted = case.requirement.asserted_fields()
    payload = {
        "requirement": case.requirement.to_json(),
        "confidence": dict.fromkeys(asserted, 0.99),
        "sources": {name: list(case.evidence[name]) for name in asserted},
        "ambiguous_fields": list(case.ambiguous_fields),
    }
    return Prediction(
        parsed=parse_model_output(payload, case.message),
        provider="fake",
        model="gold-echo",
    )


def run_fake_eval(
    cases: Sequence[GoldCase],
    injection_cases: Sequence[InjectionCase],
    *,
    prompt_version: int,
    prompt_name: str = PROMPT_NAME,
) -> EvalResult:
    return evaluate(
        cases,
        [_gold_echo(case) for case in cases],
        mode="fake",
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        injection_cases=injection_cases,
    )


def run_provider_eval(
    cases: Sequence[GoldCase],
    injection_cases: Sequence[InjectionCase],
    *,
    parser: InquiryParser,
    db: Session,
    prompt_name: str = PROMPT_NAME,
    prompt_version: int,
    tenant_id: UUID = DEFAULT_TENANT_ID,
) -> EvalResult:
    predictions: list[Prediction] = []
    for case in cases:
        try:
            outcome = parser.parse(
                db,
                message=case.message,
                channel=case.channel,
                language_hint=case.language,
                tenant_id=tenant_id,
            )
        except SilentAssumptionError as exc:
            predictions.append(
                Prediction(
                    parsed=None,
                    error=f"SilentAssumptionError: {exc}",
                    silently_assumed=True,
                )
            )
        except (RequirementParseError, LLMError) as exc:
            predictions.append(Prediction(parsed=None, error=f"{type(exc).__name__}: {exc}"))
        else:
            predictions.append(
                Prediction(
                    parsed=outcome.parsed,
                    provider=outcome.provider,
                    model=outcome.model,
                )
            )
    return evaluate(
        cases,
        predictions,
        mode="provider",
        prompt_name=prompt_name,
        prompt_version=prompt_version,
        injection_cases=injection_cases,
    )


# ---------------------------------------------------------------------------
# Result files and CLI
# ---------------------------------------------------------------------------


def write_result(result: EvalResult, results_dir: Path) -> Path:
    """Write one result per prompt version; never downgrade provider to fake."""

    results_dir.mkdir(parents=True, exist_ok=True)
    path = results_dir / f"{result.prompt_name}_v{result.prompt_version}.json"
    if path.exists():
        existing = json.loads(path.read_text(encoding="utf-8"))
        existing_mode = existing.get("mode") if isinstance(existing, Mapping) else None
        if existing_mode == "provider" and result.mode != "provider":
            raise ValueError(
                f"{path} holds a provider result; refusing to overwrite it with {result.mode}"
            )
    path.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _arguments(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("fake", "provider"), required=True)
    parser.add_argument("--gold", type=Path, default=Path("data/eval/inquiry_gold.jsonl"))
    parser.add_argument("--injection", type=Path, default=Path("data/eval/inquiry_injection.jsonl"))
    parser.add_argument("--results-dir", type=Path, default=Path("data/eval/results"))
    parser.add_argument(
        "--database-url",
        default=None,
        help="provider mode only; defaults to DATABASE_URL",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _arguments(argv)
    cases = load_gold(args.gold)
    for case in cases:
        validate_gold_case(case)
    injections = load_injection(args.injection)

    settings = get_settings()
    prompt_version = PromptRegistry(Path(settings.prompts_dir)).get(PROMPT_NAME).version
    if args.mode == "fake":
        result = run_fake_eval(cases, injections, prompt_version=prompt_version)
    else:
        if args.database_url:
            settings = settings.model_copy(update={"database_url": args.database_url})
        gateway = build_gateway(settings)
        if any(provider.name == "fake" for provider in gateway.providers):
            raise SystemExit(
                "provider mode needs a real model: set LLM_PROVIDER (or "
                "LLM_PROVIDER_CHAIN) to the owner-configured provider"
            )
        engine = build_engine(settings.effective_database_url)
        try:
            factory = build_session_factory(engine)
            with factory() as db:
                result = run_provider_eval(
                    cases,
                    injections,
                    parser=InquiryParser(gateway),
                    db=db,
                    prompt_version=prompt_version,
                )
                db.commit()
        finally:
            engine.dispose()

    path = write_result(result, args.results_dir)
    print(
        f"{result.mode} eval: accuracy={result.required_field_accuracy:.4f} "
        f"silent={result.silent_assumptions} parse_failures={result.parse_failures} "
        f"gate={result.gate['status']} -> {path}"
    )
    return 0 if result.gate.get("passed") is not False else 1


if __name__ == "__main__":
    raise SystemExit(main())
