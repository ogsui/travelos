"""Hand-checked package gold set and deterministic component match (task 013b1).

The gold set is derived by hand from the normalized offers and the builder's
documented rules, not copied from a builder run. ``pkg_001`` is worked through
here so the traceability is reviewable:

* 2 adults + 1 child aged 6 and 2 requested rooms pass occupancy (minimum 1);
* a flight/transfer is per traveller (3), a hotel per booked room (2);
* the return leg is added because the requirement has an end date;
* 3 x 1,000,000 + 3 x 100,000 + 2 x 900,000 + 3 x 1,000,000 = 8,100,000 paise.

A run that silently changes a quantity, drops a component or picks a different
offer fails the exact-case assertion below.
"""

from __future__ import annotations

import dataclasses

from travelos.packages.eval import (
    GOLD_NOW,
    REQUIRED_COMPONENT_MATCH_RATE,
    load_gold,
    run_eval,
)


def test_gold_set_covers_the_builder_paths() -> None:
    cases = load_gold()

    assert len(cases) >= 10
    assert len({case.id for case in cases}) == len(cases)
    assert {case.category for case in cases} >= {
        "family package",
        "multi-city package",
        "one-way flight",
        "package with activity",
        "visa only",
        "invalid occupancy",
        "sold out hotel",
    }
    for case in cases:
        for expected in case.expected_components:
            offer_ids = {offer.offer_id for offer in case.offers}
            assert expected.offer_id in offer_ids, case.id
        if case.expected_problems:
            assert case.expected_components == (), case.id
            assert case.expected_total is None, case.id
        else:
            assert case.expected_components, case.id
            assert case.expected_total is not None, case.id


def test_the_first_case_is_hand_checkable() -> None:
    case = load_gold()[0]

    assert case.id == "pkg_001"
    assert [(item.offer_id, item.quantity) for item in case.expected_components] == [
        ("air-out", 3),
        ("transfer-goa", 3),
        ("hotel-goa", 2),
        ("air-back", 3),
    ]
    assert case.expected_total == 8_100_000


def test_the_deterministic_builder_meets_the_component_gate() -> None:
    result = run_eval(now=GOLD_NOW)

    assert result.component_match_rate >= REQUIRED_COMPONENT_MATCH_RATE
    assert result.component_match_rate == 1.0
    assert result.problem_accuracy == 1.0
    assert result.exact_case_rate == 1.0


def test_every_gold_total_is_exact() -> None:
    result = run_eval(now=GOLD_NOW)

    assert result.cost_match_rate == 1.0
    assert result.gate["passed"] is True


def test_a_changed_quantity_is_reported_as_a_mismatch() -> None:
    first = load_gold()[0]
    broken = dataclasses.replace(
        first,
        expected_components=tuple(
            dataclasses.replace(item, quantity=1) for item in first.expected_components
        ),
    )

    result = run_eval([broken], now=GOLD_NOW)

    assert result.component_match_rate < 1.0
    assert result.exact_case_rate == 0.0
    assert result.failures[0]["case_id"] == "pkg_001"
    actual = {
        (item["offer_id"], item["quantity"]) for item in result.failures[0]["actual_components"]
    }
    assert actual == {
        ("air-out", 3),
        ("transfer-goa", 3),
        ("hotel-goa", 2),
        ("air-back", 3),
    }
