"""Adversarial dataset eval (task 007).

The dataset is data, not a model test: it proves the fixtures used by the later
eval harness are complete, obviously synthetic and unusable as instructions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.eval

FIXTURES = Path(__file__).resolve().parents[2] / "data" / "eval" / "adversarial_messages.jsonl"


def load_cases() -> list[dict[str, Any]]:
    return [
        json.loads(line)
        for line in FIXTURES.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_adversarial_fixtures_are_complete_and_obviously_fake() -> None:
    cases = load_cases()
    assert len(cases) >= 18

    categories = {case["category"] for case in cases}
    assert categories == {
        "prompt_injection",
        "email_injection",
        "scraped_page_injection",
        "abuse",
        "pii_fishing",
        "refund_demand",
        "fake_urgency",
        "wrong_language",
        "giant_message",
    }

    for case in cases:
        assert case["input"].strip()
        assert case["expected_behavior"] in {
            "treat_as_data",
            "refuse_and_escalate",
            "ask_clarification",
            "handoff_human",
            "ignore_instruction",
        }
        assert case["must_not"]
        assert "@gmail.com" not in case["input"]
        assert "+91 9" not in case["input"]

    assert any(len(case["input"]) > 4000 for case in cases if case["category"] == "giant_message")
    assert any(
        "ignore previous instructions" in case["input"].lower()
        for case in cases
        if case["category"] == "prompt_injection"
    )
