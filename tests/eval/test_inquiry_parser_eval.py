"""Hand-checked inquiry-parser gold set and eval runner (task 010d).

These tests keep the fixtures honest (every gold value traces to its message),
prove the fake-mode plumbing end to end, and check that a scoring error is
reported rather than hidden. The accuracy gate itself is only meaningful in
provider mode, which needs an owner-configured model; see
``tests/integration/test_inquiry_eval_provider.py`` and
``docs/08_TESTING_EVAL.md``.
"""

from __future__ import annotations

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest

from travelos.inquiry.eval import (
    PROMPT_NAME,
    GoldCase,
    Prediction,
    evaluate,
    load_gold,
    load_injection,
    run_fake_eval,
    run_injection_cases,
    validate_gold_case,
    write_result,
)
from travelos.inquiry.parser import parse_model_output
from travelos.inquiry.requirement import Requirement
from travelos.llm.prompts import PromptRegistry

pytestmark = pytest.mark.eval

REPO_ROOT = Path(__file__).resolve().parents[2]
GOLD = REPO_ROOT / "data" / "eval" / "inquiry_gold.jsonl"
INJECTION = REPO_ROOT / "data" / "eval" / "inquiry_injection.jsonl"
SYNTHETIC = REPO_ROOT / "data" / "synthetic" / "inquiries.jsonl"
RESULTS = REPO_ROOT / "data" / "eval" / "results"


def synthetic_categories() -> set[str]:
    return {
        json.loads(line)["category"]
        for line in SYNTHETIC.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def echo_prediction(case: GoldCase) -> Prediction:
    asserted = case.requirement.asserted_fields()
    payload = {
        "requirement": case.requirement.to_json(),
        "confidence": dict.fromkeys(asserted, 0.99),
        "sources": {name: list(case.evidence[name]) for name in asserted},
        "ambiguous_fields": list(case.ambiguous_fields),
    }
    return Prediction(parsed=parse_model_output(payload, case.message))


def test_gold_covers_every_synthetic_category_and_traces_to_the_message() -> None:
    cases = load_gold(GOLD)
    for case in cases:
        validate_gold_case(case)

    counts: dict[str, int] = {}
    for case in cases:
        counts[case.category] = counts.get(case.category, 0) + 1
    assert set(counts) == synthetic_categories()
    assert all(count >= 2 for count in counts.values())
    assert len({case.id for case in cases}) == len(cases)


def test_gold_quotes_are_verbatim_slices_of_the_message() -> None:
    cases = load_gold(GOLD)
    for case in cases:
        for field, quotes in case.evidence.items():
            for quote in quotes:
                assert quote in case.message, f"{case.id}: {field} quote {quote!r}"
                assert quote.strip() == quote
                assert field in case.requirement.asserted_fields()


def test_untraceable_gold_is_rejected() -> None:
    case = GoldCase(
        id="bad_gold",
        category="domestic_flight",
        language="en",
        channel="whatsapp",
        message="Flight from Mumbai to Goa on 2026-12-05 for 2 adults.",
        requirement=Requirement(
            trip_type="flight",
            origin_city="Mumbai",
            destinations=["Goa"],
            start_date="2026-12-05",
            adults=2,
        ),
        evidence={
            "trip_type": ("Flight",),
            "origin_city": ("Mumbai",),
            "destinations": ("Goa",),
            "start_date": ("2026-12-05",),
            "adults": ("2 adults",),
        },
    )
    validate_gold_case(case)

    missing = replace(case, evidence={**case.evidence, "adults": ("4 adults",)})
    with pytest.raises(ValueError, match="is not in the message"):
        validate_gold_case(missing)

    incomplete = replace(case, evidence={"trip_type": ("Flight",)})
    with pytest.raises(ValueError, match="do not match asserted fields"):
        validate_gold_case(incomplete)


def test_fake_mode_exercises_the_plumbing_but_is_not_a_measurement() -> None:
    cases = load_gold(GOLD)
    injections = load_injection(INJECTION)
    version = PromptRegistry(REPO_ROOT / "prompts").get(PROMPT_NAME).version

    result = run_fake_eval(cases, injections, prompt_version=version)

    assert result.case_count == len(cases)
    assert result.required_field_accuracy == 1.0
    assert result.recall == 1.0
    assert result.precision == 1.0
    assert result.missing_info_accuracy == 1.0
    assert result.parse_failures == 0
    assert result.silent_assumptions == 0
    assert result.injection["contained"] == result.injection["total"]
    assert result.injection["action_capable"] is False
    assert result.gate["status"] == "not_measured"
    assert result.gate["passed"] is None
    assert "not model accuracy" in str(result.gate["reason"])


def test_scorer_reports_a_missed_required_field() -> None:
    cases = load_gold(GOLD)
    injections = load_injection(INJECTION)
    case = cases[0]

    prediction = echo_prediction(case)
    assert prediction.parsed is not None
    wrong = prediction.parsed.requirement.model_copy(update={"adults": 99})
    assert prediction.parsed is not None
    prediction = Prediction(
        parsed=prediction.parsed.model_copy(update={"requirement": wrong}),
        provider="fake",
        model="gold-echo",
    )
    others = [echo_prediction(item) for item in cases[1:]]

    result = evaluate(
        cases,
        [prediction, *others],
        mode="provider",
        prompt_name=PROMPT_NAME,
        prompt_version=1,
        injection_cases=injections,
    )

    assert result.required_field_accuracy < 1.0
    assert result.recall < 1.0
    assert result.per_field["adults"]["missed"] >= 1
    assert result.per_field["adults"]["wrong"] >= 1
    assert any(
        failure.get("id") == case.id and failure.get("field") == "adults"
        for failure in result.failures
    )
    # One miss in a 30+ field gold set is still above the 95% gate; the gate is
    # checked separately below.
    assert result.gate["status"] in {"passed", "failed"}


def test_gate_fails_when_accuracy_drops_below_the_threshold() -> None:
    cases = load_gold(GOLD)
    injections = load_injection(INJECTION)
    predictions: list[Prediction] = []
    for index, case in enumerate(cases):
        prediction = echo_prediction(case)
        assert prediction.parsed is not None
        if index < 10:
            prediction = Prediction(
                parsed=prediction.parsed.model_copy(
                    update={
                        "requirement": prediction.parsed.requirement.model_copy(
                            update={"adults": 99}
                        )
                    }
                ),
                provider="fake",
                model="gold-echo",
            )
        predictions.append(prediction)

    result = evaluate(
        cases,
        predictions,
        mode="provider",
        prompt_name=PROMPT_NAME,
        prompt_version=1,
        injection_cases=injections,
    )

    assert result.required_field_accuracy < 0.95
    assert result.gate["status"] == "failed"
    assert result.gate["passed"] is False
    assert "below" in str(result.gate["reason"])


def test_scorer_reports_an_extra_field() -> None:
    cases = load_gold(GOLD)
    injections = load_injection(INJECTION)
    case = next(item for item in cases if item.id == "gold_hindi_02")
    assert case.requirement.end_date is None

    prediction = echo_prediction(case)
    assert prediction.parsed is not None
    confident = dict(prediction.parsed.confidence)
    confident["end_date"] = 0.9
    prediction = Prediction(
        parsed=prediction.parsed.model_copy(
            update={
                "requirement": prediction.parsed.requirement.model_copy(
                    update={"end_date": "2026-12-25"}
                ),
                "confidence": confident,
                "spans": {
                    **prediction.parsed.spans,
                    "end_date": prediction.parsed.spans["start_date"],
                },
            }
        ),
        provider="fake",
        model="gold-echo",
    )
    predictions = [prediction if item.id == case.id else echo_prediction(item) for item in cases]

    result = evaluate(
        cases,
        predictions,
        mode="provider",
        prompt_name=PROMPT_NAME,
        prompt_version=1,
        injection_cases=injections,
    )

    assert result.per_field["end_date"]["extra"] >= 1
    assert result.precision < 1.0


def test_every_injection_fixture_is_contained() -> None:
    cases = load_injection(INJECTION)
    assert len(cases) >= 4
    assert {case.expected for case in cases} == {
        "RequirementParseError",
        "SilentAssumptionError",
    }

    outcome = run_injection_cases(cases)

    assert outcome["contained"] == outcome["total"]
    assert outcome["failures"] == []
    assert outcome["action_capable"] is False
    assert all(isinstance(case.payload, dict) for case in cases)


def test_an_extra_top_level_key_cannot_smuggle_an_action() -> None:
    case = next(item for item in load_injection(INJECTION) if "action" in str(item.payload))
    with pytest.raises(Exception, match="unexpected parser output fields"):
        parse_model_output(case.payload, case.message)


def test_result_writer_refuses_to_downgrade_a_provider_result(tmp_path: Path) -> None:
    cases = load_gold(GOLD)
    injections = load_injection(INJECTION)
    version = PromptRegistry(REPO_ROOT / "prompts").get(PROMPT_NAME).version
    fake = run_fake_eval(cases, injections, prompt_version=version)

    provider = replace(fake, mode="provider", gate={"status": "passed", "passed": True})
    path = write_result(provider, tmp_path)
    assert path.name == f"{PROMPT_NAME}_v{version}.json"

    with pytest.raises(ValueError, match="refusing to overwrite"):
        write_result(fake, tmp_path)


def test_committed_result_matches_the_prompt_version() -> None:
    version = PromptRegistry(REPO_ROOT / "prompts").get(PROMPT_NAME).version
    path = RESULTS / f"{PROMPT_NAME}_v{version}.json"
    assert path.is_file(), (
        f"missing {path.relative_to(REPO_ROOT)}: a copy of the prompt exists without an eval "
        "result. Run `python -m travelos.inquiry.eval --mode fake`."
    )

    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["prompt_name"] == PROMPT_NAME
    assert document["prompt_version"] == version
    assert document["mode"] in {"fake", "provider"}
    assert document["case_count"] == len(load_gold(GOLD))
    assert document["injection"]["contained"] == document["injection"]["total"]
    assert document["silent_assumptions"] == 0
    if document["mode"] == "fake":
        assert document["gate"]["status"] == "not_measured"
        assert document["provider"] == "fake"


def test_injection_payloads_are_obviously_fake_text() -> None:
    for line in INJECTION.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        document = json.loads(line)
        assert re.match(r"^inj_", document["id"])
        assert document["expected"] in {"RequirementParseError", "SilentAssumptionError"}
