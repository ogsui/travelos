"""Provider-mode eval plumbing against a scripted provider (task 010d).

The real accuracy baseline needs the owner's model, so it cannot run in CI. This
test drives the same code path -- ``run_provider_eval`` through the real gateway
and database -- with the deterministic fake provider. It proves the plumbing and
the ``llm_calls`` provenance, not model quality.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

from travelos.inquiry.eval import load_gold, load_injection, run_provider_eval
from travelos.inquiry.parser import InquiryParser
from travelos.llm.gateway import LLMGateway
from travelos.llm.models import LLMCall
from travelos.llm.prompts import PromptRegistry
from travelos.llm.providers.fake import FakeProvider

pytestmark = pytest.mark.integration

REPO_ROOT = Path(__file__).resolve().parents[2]
PROMPTS = REPO_ROOT / "prompts"
GOLD = REPO_ROOT / "data" / "eval" / "inquiry_gold.jsonl"
INJECTION = REPO_ROOT / "data" / "eval" / "inquiry_injection.jsonl"


def scripted_output(case: Any) -> str:
    asserted = case.requirement.asserted_fields()
    return json.dumps(
        {
            "requirement": case.requirement.to_json(),
            "confidence": dict.fromkeys(asserted, 0.95),
            "sources": {name: list(case.evidence[name]) for name in asserted},
            "ambiguous_fields": list(case.ambiguous_fields),
        }
    )


def test_provider_mode_scores_every_case_and_records_llm_calls(db: Session) -> None:
    cases = load_gold(GOLD)
    injections = load_injection(INJECTION)
    provider = FakeProvider(responses=[scripted_output(case) for case in cases])
    gateway = LLMGateway(
        providers=[provider],
        prompts=PromptRegistry(PROMPTS),
        max_attempts=1,
        backoff_seconds=0.01,
        sleep=lambda _seconds: None,
    )

    result = run_provider_eval(
        cases,
        injections,
        parser=InquiryParser(gateway),
        db=db,
        prompt_version=1,
    )

    assert result.mode == "provider"
    assert result.case_count == len(cases)
    assert result.required_field_accuracy == 1.0
    assert result.silent_assumptions == 0
    assert result.parse_failures == 0
    assert result.injection["contained"] == result.injection["total"]
    # A scripted provider is not a real measurement; the gate only asserts that
    # the provider-mode scoring path is wired correctly.
    assert result.gate["status"] == "passed"
    assert result.provider == "fake"
    assert provider.calls == len(cases)

    db.flush()
    rows = list(db.execute(select(LLMCall).order_by(LLMCall.id)).scalars())
    assert len(rows) == len(cases)
    assert {row.status for row in rows} == {"success"}
    assert {row.prompt_name for row in rows} == {"inquiry_parser"}
    assert {row.prompt_version for row in rows} == {1}
    assert {row.data_class for row in rows} == {"customer"}
