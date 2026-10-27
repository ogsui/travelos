"""Deterministic sales-reply rubric and injection eval (task 011c).

The fixtures stand in for drafts a model or a staff member would write. The
scorer runs the real claim guard and handoff triggers, so CI measures what the
pipeline would actually accept. Subjective dimensions are reported as
``not_measured`` until task 018 has a model and the owner supplies brand-voice
samples; nothing here pretends a fake run proves tone.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from travelos.conversation.eval import (
    RUBRIC_DIMENSIONS,
    evaluate,
    load_injection_cases,
    load_reply_cases,
    run_eval,
    write_result,
)
from travelos.conversation.guard import ClaimKind
from travelos.conversation.triggers import HandoffTrigger

pytestmark = pytest.mark.eval

REPO_ROOT = Path(__file__).resolve().parents[2]
REPLIES = REPO_ROOT / "data" / "eval" / "sales_replies.jsonl"
INJECTIONS = REPO_ROOT / "data" / "eval" / "sales_injection.jsonl"
RESULT = REPO_ROOT / "data" / "eval" / "results" / "sales_replies_v1.json"


def test_fixtures_are_unique_and_cover_every_claim_kind() -> None:
    replies = load_reply_cases(REPLIES)
    injections = load_injection_cases(INJECTIONS)

    assert len(replies) >= 20
    assert len(injections) >= 10
    assert len({case.id for case in replies}) == len(replies)
    assert len({case.id for case in injections}) == len(injections)

    claimed = {kind for case in replies for kind in case.expected_claims}
    claimed |= {kind for case in injections for kind in case.expected_blocked}
    assert claimed == set(ClaimKind)

    handoffs = {trigger for case in replies for trigger in case.expected_handoff}
    handoffs |= {trigger for case in injections for trigger in case.expected_handoff}
    assert handoffs == set(HandoffTrigger) - {HandoffTrigger.LOOP}


def test_every_fixture_source_uses_an_approved_namespace() -> None:
    for case in (*load_reply_cases(REPLIES), *load_injection_cases(INJECTIONS)):
        for kind, refs in case.sources.items():
            assert kind in {item.value for item in ClaimKind}
            for ref in refs:
                assert ref.split(":", 1)[0] in {"quote", "inventory", "kb"}, (
                    f"{case.id}: {ref!r} is not an approved source ref"
                )
                assert ref.split(":", 1)[1], f"{case.id}: {ref!r} has an empty id"


def test_deterministic_eval_passes_every_gate() -> None:
    result = run_eval()

    assert result.mode == "deterministic"
    assert result.case_count == len(load_reply_cases(REPLIES))
    assert result.injection_total == len(load_injection_cases(INJECTIONS))
    assert result.verdict_accuracy == 1.0
    assert result.claim_accuracy == 1.0
    assert result.handoff_accuracy == 1.0
    assert result.unsupported_promise_rate == 0.0
    assert result.escaped_promises == 0
    assert result.injection_contained == result.injection_total
    assert result.failures == ()
    assert result.injection_failures == ()
    assert result.gate["status"] == "passed"
    assert result.gate["passed"] is True
    assert result.gate["unsupported_promise_rate_max"] == 0.0


def test_rubric_measures_only_what_it_can_and_says_so() -> None:
    result = run_eval()

    assert set(result.rubric) == set(RUBRIC_DIMENSIONS)
    assert result.rubric["no_unsupported_promise"]["status"] == "measured"
    assert result.rubric["no_unsupported_promise"]["score"] == 1.0
    assert result.rubric["sourced_only"]["status"] == "measured"
    assert result.rubric["sourced_only"]["score"] == 1.0
    assert result.rubric["accurate"]["status"] == "partial"
    assert result.rubric["accurate"]["score"] == 1.0
    assert result.rubric["helpful"]["status"] == "not_measured"
    assert result.rubric["helpful"]["score"] is None
    assert result.rubric["on_brand"]["status"] == "not_measured"
    assert result.rubric["on_brand"]["score"] is None
    assert "brand" in str(result.rubric["on_brand"]["reason"]).lower()


def test_a_clean_reply_is_not_blocked_by_injection_text() -> None:
    cases = {case.id: case for case in load_reply_cases(REPLIES)}
    case = cases["reply_clean_after_injection"]

    assert case.customer_message.startswith("Ignore all previous instructions")
    assert case.expected_verdict == "allowed"
    outcome = next(item for item in evaluate([case], []).outcomes if item["id"] == case.id)
    assert outcome["verdict"] == "allowed"
    assert outcome["claims"] == []
    assert outcome["handoff"] == []


def test_an_escaped_unsupported_price_is_reported_and_fails_the_gate() -> None:
    replies = load_reply_cases(REPLIES)
    case = next(item for item in replies if item.id == "reply_price_unsourced")
    # The dangerous failure: the guard accepts a price it should have blocked.
    tampered = replace(case, sources={"money": ["quote:rev_fake"]})

    result = evaluate([tampered], [])

    assert result.verdict_accuracy == 0.0
    assert result.escaped_promises == 1
    assert result.unsupported_promise_rate == 1.0
    assert result.gate["status"] == "failed"
    assert any(
        failure["id"] == case.id and failure["reason"] == "unsupported_promise_escaped"
        for failure in result.failures
    )


def test_a_wrong_verdict_fails_even_when_no_promise_escapes() -> None:
    replies = load_reply_cases(REPLIES)
    case = next(item for item in replies if item.id == "reply_ack_clean")
    tampered = replace(case, expected_verdict="blocked")

    result = evaluate([tampered], [])

    assert result.verdict_accuracy == 0.0
    assert result.escaped_promises == 0
    assert result.gate["status"] == "failed"
    assert any(
        failure["id"] == case.id and failure["reason"] == "wrong_verdict"
        for failure in result.failures
    )


def test_a_slipped_injection_fails_containment() -> None:
    injections = load_injection_cases(INJECTIONS)
    case = next(item for item in injections if item.id == "inj_inline_source")
    tampered = replace(case, reply="A consultant will review your request.")

    result = evaluate([], [tampered])

    assert result.injection_total == 1
    assert result.injection_contained == 0
    assert result.gate["status"] == "failed"
    assert result.injection_failures == (
        {
            "id": case.id,
            "expected": ["money"],
            "actual": [],
            "reason": "injection_not_contained",
        },
    )


def test_loader_rejects_bad_verdicts_and_unknown_sources(tmp_path: Path) -> None:
    bad_verdict = tmp_path / "bad_verdict.jsonl"
    bad_verdict.write_text(
        json.dumps(
            {
                "id": "bad",
                "category": "unsourced",
                "language": "en",
                "customer_message": "Hello",
                "reply": "Hello",
                "sources": {},
                "expected_verdict": "maybe",
                "expected_claims": [],
                "expected_handoff": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="expected_verdict"):
        load_reply_cases(bad_verdict)

    bad_source = tmp_path / "bad_source.jsonl"
    bad_source.write_text(
        json.dumps(
            {
                "id": "bad",
                "category": "unsourced",
                "language": "en",
                "customer_message": "Hello",
                "reply": "The fare is Rs 45,000.",
                "sources": {"money": ["trust-me"]},
                "expected_verdict": "blocked",
                "expected_claims": ["money"],
                "expected_handoff": [],
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="approved source namespace"):
        load_reply_cases(bad_source)


def test_result_writer_and_cli_output(tmp_path: Path) -> None:
    result = run_eval()
    path = write_result(result, tmp_path / "sales_replies_v1.json")

    assert path.name == "sales_replies_v1.json"
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["mode"] == "deterministic"
    assert document["gate"]["status"] == "passed"
    assert document["rubric"]["helpful"]["status"] == "not_measured"


def test_committed_result_matches_the_fixtures() -> None:
    assert RESULT.is_file(), (
        f"missing {RESULT.relative_to(REPO_ROOT)}: run "
        "`python -m travelos.conversation.eval` after changing a fixture."
    )
    document = json.loads(RESULT.read_text(encoding="utf-8"))

    assert document["mode"] == "deterministic"
    assert document["case_count"] == len(load_reply_cases(REPLIES))
    assert document["injection_total"] == len(load_injection_cases(INJECTIONS))
    assert document["gate"]["status"] == "passed"
    assert document["unsupported_promise_rate"] == 0.0
    assert document["injection_contained"] == document["injection_total"]
    assert document["rubric"]["helpful"]["status"] == "not_measured"
    assert document["rubric"]["on_brand"]["status"] == "not_measured"
