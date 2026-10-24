"""Adversarial prompt-injection eval (task 006).

Each fixture simulates a fully compromised model that follows the injected
instruction. The eval proves three things without a database or network:

* untrusted text cannot close the DATA block or smuggle chat control tokens;
* every compromised output fails the declared JSON schema and allow-list;
* the request type has no tool/function field, so injected text has no path to
  an action even if the model obeys it.

The DB-backed counterpart lives in
``tests/integration/test_llm_gateway.py``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from travelos.llm.injection import UNTRUSTED_CLOSE, delimit_untrusted
from travelos.llm.schema import parse_json_output, validate_json
from travelos.llm.types import LLMRequest

pytestmark = pytest.mark.eval

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "eval" / "llm_injection.jsonl"

SCHEMA = {
    "type": "object",
    "properties": {"intent": {"type": "string", "enum": ["book", "cancel"]}},
    "required": ["intent"],
    "additionalProperties": False,
}


def load_cases() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in FIXTURES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_every_injection_case_is_delimited_and_rejected() -> None:
    cases = load_cases()
    assert len(cases) >= 5

    for case in cases:
        wrapped = delimit_untrusted(case["input"], label="message")
        assert wrapped.count(UNTRUSTED_CLOSE) == 1, case["id"]
        assert "<|im_start|>" not in wrapped, case["id"]
        assert "[INST]" not in wrapped, case["id"]

        parsed = parse_json_output(case["compromised_output"])
        errors = ["output is not valid JSON"] if parsed is None else validate_json(parsed, SCHEMA)
        assert errors, case["id"]

    assert set(LLMRequest.__dataclass_fields__) == {
        "messages",
        "temperature",
        "max_tokens",
    }
