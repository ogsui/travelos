"""Synthetic dataset contract: determinism, schemas, fake patterns and coverage."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from travelos.llm.schema import validate_json
from travelos.synthetic import DEFAULT_SEED, generate_dataset, write_dataset

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_DIR = REPO_ROOT / "data"
SCHEMA_DIR = DATA_DIR / "schemas"
SYNTHETIC_DIR = DATA_DIR / "synthetic"
EVAL_DIR = DATA_DIR / "eval"

SCHEMAS = {
    "customers.jsonl": "customer.schema.json",
    "inquiries.jsonl": "inquiry.schema.json",
    "quotes_gold.jsonl": "quote.schema.json",
    "supplier_inventory.jsonl": "supplier_inventory.schema.json",
    "supplier_scenarios.jsonl": "supplier_scenario.schema.json",
    "competitor_snapshots.jsonl": "competitor_snapshot.schema.json",
    "channel_webhooks.jsonl": "channel_webhook.schema.json",
    "payments.jsonl": "payment.schema.json",
    "marketing_history.jsonl": "marketing_history.schema.json",
}
EVAL_SCHEMAS = {"adversarial_messages.jsonl": "adversarial_message.schema.json"}

REQUIRED_CATEGORIES = {
    "domestic_flight",
    "international_flight",
    "family_package",
    "honeymoon",
    "pilgrimage",
    "corporate_multi_city",
    "group_mice",
    "visa_only",
    "hotel_only",
    "vague",
    "contradictory",
    "hinglish",
    "hindi",
    "date_change_midconversation",
    "misspelt_names",
    "long_layover",
}
REQUIRED_SIGNALS = {
    "date_change",
    "misspelt_name",
    "contradiction",
    "hinglish",
    "hindi",
    "long_layover",
    "infant",
    "child_age",
    "vague",
    "visa_only",
    "multi_city",
    "group",
}
FAKE_SURNAMES = {
    "Synthetic",
    "Example",
    "Sample",
    "Fixture",
    "Demo",
    "Mock",
    "Placeholder",
    "Test",
}
PHONE = re.compile(r"^\+91 00000 [0-9]{5}$")
EMAIL = re.compile(r"^[a-z0-9._-]+@example\.test$")
PASSPORT = re.compile(r"^F7[0-9]{6}$")
DISALLOWED = ("gmail.com", "yahoo.", "hotmail.", "@test.com", "+91 9", "+91 8", "+91 7")


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()
    ]


def load_schema(name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / name).read_text(encoding="utf-8"))


def validate_records(records: list[dict[str, Any]], schema_name: str) -> None:
    schema = load_schema(schema_name)
    for index, record in enumerate(records):
        errors = validate_json(record, schema)
        assert not errors, f"{schema_name} record {index}: {errors}"


def walk_strings(value: object) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from walk_strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from walk_strings(item)


def assert_fake_patterns(records: list[dict[str, Any]]) -> None:
    for record in records:
        for text in walk_strings(record):
            for bad in DISALLOWED:
                assert bad not in text, text
            if "@" in text:
                assert EMAIL.fullmatch(text), text
            if text.startswith("+91"):
                assert PHONE.fullmatch(text), text
            if text.startswith("F7"):
                assert PASSPORT.fullmatch(text), text


def committed(name: str) -> Path:
    path = SYNTHETIC_DIR / name
    assert path.is_file(), f"missing generated dataset: {path}"
    return path


def test_same_seed_is_byte_identical(tmp_path: Path) -> None:
    seed = 4242
    first = generate_dataset(seed)
    second = generate_dataset(seed)

    assert first == second
    assert first.seed == seed

    write_dataset(first, tmp_path / "a")
    write_dataset(second, tmp_path / "b")

    for path in sorted((tmp_path / "a").rglob("*.jsonl")):
        other = tmp_path / "b" / path.relative_to(tmp_path / "a")
        assert path.read_bytes() == other.read_bytes(), path.name
    assert (tmp_path / "a" / "manifest.json").read_bytes() == (
        tmp_path / "b" / "manifest.json"
    ).read_bytes()


def test_different_seed_changes_the_data(tmp_path: Path) -> None:
    first = generate_dataset(1)
    second = generate_dataset(2)

    assert first.files["customers.jsonl"] != second.files["customers.jsonl"]


def test_generated_files_match_schemas_and_manifest(tmp_path: Path) -> None:
    dataset = generate_dataset(DEFAULT_SEED)
    write_dataset(dataset, tmp_path)

    manifest = json.loads((tmp_path / "manifest.json").read_text(encoding="utf-8"))
    assert not validate_json(manifest, load_schema("manifest.schema.json"))
    assert manifest["seed"] == DEFAULT_SEED
    entries = {entry["name"]: entry for entry in manifest["files"]}

    for name, schema_name in SCHEMAS.items():
        records = read_jsonl(tmp_path / name)
        validate_records(records, schema_name)
        assert entries[name]["count"] == len(records)
        assert entries[name]["sha256"] == hashlib.sha256((tmp_path / name).read_bytes()).hexdigest()

    assert (tmp_path / "adversarial_messages.jsonl").is_file()


def test_committed_dataset_matches_manifest_and_schemas() -> None:
    manifest_path = SYNTHETIC_DIR / "manifest.json"
    assert manifest_path.is_file(), "run `make seed` to generate data/synthetic"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert not validate_json(manifest, load_schema("manifest.schema.json"))
    assert manifest["seed"] == DEFAULT_SEED
    entries = {entry["name"]: entry for entry in manifest["files"]}

    for name, schema_name in SCHEMAS.items():
        path = committed(name)
        records = read_jsonl(path)
        validate_records(records, schema_name)
        assert entries[name]["count"] == len(records), name
        assert entries[name]["sha256"] == hashlib.sha256(path.read_bytes()).hexdigest(), name

    adversarial = read_jsonl(EVAL_DIR / "adversarial_messages.jsonl")
    validate_records(adversarial, EVAL_SCHEMAS["adversarial_messages.jsonl"])


def test_dataset_coverage_and_minimum_sizes() -> None:
    customers = read_jsonl(committed("customers.jsonl"))
    inquiries = read_jsonl(committed("inquiries.jsonl"))
    quotes = read_jsonl(committed("quotes_gold.jsonl"))
    inventory = read_jsonl(committed("supplier_inventory.jsonl"))
    scenarios = read_jsonl(committed("supplier_scenarios.jsonl"))
    competitors = read_jsonl(committed("competitor_snapshots.jsonl"))
    webhooks = read_jsonl(committed("channel_webhooks.jsonl"))
    payments = read_jsonl(committed("payments.jsonl"))
    marketing = read_jsonl(committed("marketing_history.jsonl"))
    adversarial = read_jsonl(EVAL_DIR / "adversarial_messages.jsonl")

    assert len(customers) >= 500
    assert len(inquiries) >= 150
    assert len(quotes) == 40
    assert len(inventory) >= 50
    assert len(scenarios) >= 25
    assert len(competitors) >= 30
    assert len(webhooks) >= 24
    assert len(payments) >= 24
    assert len(marketing) >= 30
    assert len(adversarial) >= 18

    assert {record["category"] for record in inquiries} == REQUIRED_CATEGORIES
    assert {record["language"] for record in inquiries} == {"en", "hi", "hinglish"}
    signals = {signal for record in inquiries for signal in record["expected_signals"]}
    assert signals >= REQUIRED_SIGNALS

    family = [record for record in inquiries if record["gold_requirement"]["infants"] > 0]
    assert family, "family-travel edge cases must include infants"
    assert any(record["gold_requirement"]["children_ages"] for record in inquiries)
    assert any(record["category"] == "misspelt_names" for record in inquiries)
    assert any(record["category"] == "date_change_midconversation" for record in inquiries)
    assert any(record["category"] == "contradictory" for record in inquiries)


def test_customer_consent_and_traveller_edge_cases() -> None:
    customers = read_jsonl(committed("customers.jsonl"))

    statuses = {consent["status"] for customer in customers for consent in customer["consents"]}
    assert statuses == {"granted", "withdrawn"}
    assert any(contact["suppressed"] for customer in customers for contact in customer["contacts"])
    assert len({customer["segment"] for customer in customers}) >= 6
    assert any(customer["household_id"] for customer in customers)

    travellers = [traveller for customer in customers for traveller in customer["travellers"]]
    assert any(traveller["is_minor"] for traveller in travellers)
    assert any(traveller["age_years"] < 2 for traveller in travellers)
    assert any(traveller["passport_no"] for traveller in travellers)

    for customer in customers:
        contact_ids = {contact["id"] for contact in customer["contacts"]}
        primaries = [contact for contact in customer["contacts"] if contact["primary"]]
        assert len(primaries) == 1, customer["id"]
        assert customer["display_name"].split()[-1] in FAKE_SURNAMES
        assert all(consent["contact_id"] in contact_ids for consent in customer["consents"])
        for consent in customer["consents"]:
            if consent["status"] == "withdrawn":
                assert consent["withdrawn_at"] is not None
            else:
                assert consent["withdrawn_at"] is None


def test_gold_quote_arithmetic_is_integer_and_consistent() -> None:
    quotes = read_jsonl(committed("quotes_gold.jsonl"))

    for quote in quotes:
        components = quote["components"]
        totals = quote["totals"]
        assert sum(item["cost_paise"] for item in components) == totals["cost_paise"]
        assert sum(item["price_paise"] for item in components) == totals["price_paise"]
        assert sum(item["tax_paise"] for item in components) == totals["tax_paise"]
        assert totals["total_paise"] == totals["price_paise"] + totals["tax_paise"]
        assert totals["margin_paise"] == totals["price_paise"] - totals["cost_paise"]
        assert totals["margin_paise"] > 0
        assert totals["margin_bps"] == (totals["margin_paise"] * 10_000 // totals["price_paise"])
        assert quote["expected"]["floor_price_paise"] <= totals["price_paise"]
        assert quote["expected"]["recommended_price_paise"] == totals["price_paise"]
        for item in components:
            assert item["price_paise"] >= item["cost_paise"]
            assert isinstance(item["cost_paise"], int) and not isinstance(item["cost_paise"], bool)


def test_supplier_competitor_channel_and_payment_scenarios() -> None:
    inventory = read_jsonl(committed("supplier_inventory.jsonl"))
    scenarios = read_jsonl(committed("supplier_scenarios.jsonl"))
    competitors = read_jsonl(committed("competitor_snapshots.jsonl"))
    webhooks = read_jsonl(committed("channel_webhooks.jsonl"))
    payments = read_jsonl(committed("payments.jsonl"))

    assert any(
        point["available_count"] == 0 for item in inventory for point in item["price_points"]
    )
    assert any(
        item["price_points"][0]["net_paise"] != item["price_points"][-1]["net_paise"]
        for item in inventory
    )
    assert {scenario["scenario"] for scenario in scenarios} >= {
        "price_change",
        "sold_out",
        "timeout",
        "partial_failure",
        "hold_expiry",
    }

    assert {snapshot["status"] for snapshot in competitors} == {
        "ok",
        "stale",
        "blocked",
        "unavailable",
    }
    assert any(snapshot["comparability_score"] < 60 for snapshot in competitors)

    assert {webhook["expected_handling"] for webhook in webhooks} >= {
        "accept",
        "dedupe",
        "stop",
        "media_download",
        "reject_signature",
    }
    assert any(webhook["out_of_order"] for webhook in webhooks)

    assert {payment["kind"] for payment in payments} == {
        "full",
        "partial",
        "failed",
        "duplicate",
        "late",
        "refund",
    }
    assert any(event["duplicate"] for payment in payments for event in payment["webhook_events"])


def test_adversarial_set_covers_every_category() -> None:
    adversarial = read_jsonl(EVAL_DIR / "adversarial_messages.jsonl")

    assert {case["category"] for case in adversarial} == {
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
    giant = [case for case in adversarial if case["category"] == "giant_message"]
    assert giant and all(len(case["input"]) > 4000 for case in giant)


def test_every_pii_like_value_uses_a_reserved_fake_pattern() -> None:
    for name in SCHEMAS:
        assert_fake_patterns(read_jsonl(committed(name)))
    assert_fake_patterns(read_jsonl(EVAL_DIR / "adversarial_messages.jsonl"))
