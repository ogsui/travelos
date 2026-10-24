"""Gateway integration: policy, repair, fallback, cost, redaction and audit."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from travelos.core.clock import Clock, ManualClock
from travelos.core.errors import AppendOnlyError
from travelos.core.models import AuditLog, HumanTask
from travelos.llm.cache import ResponseCache
from travelos.llm.errors import (
    AllProvidersFailedError,
    AuthProviderError,
    LLMBudgetExceededError,
    LLMPolicyViolationError,
    LLMSchemaError,
)
from travelos.llm.gateway import LLMGateway
from travelos.llm.injection import UNTRUSTED_CLOSE
from travelos.llm.models import LLMCall
from travelos.llm.prompts import PromptRegistry
from travelos.llm.providers.base import LLMProvider
from travelos.llm.providers.fake import FakeProvider
from travelos.llm.types import DataClass, ProviderPolicy, ProviderPricing

pytestmark = pytest.mark.integration

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "intent": {"type": "string", "enum": ["book", "cancel"]},
        "note": {"type": "string"},
    },
    "required": ["intent"],
    "additionalProperties": False,
}
PROMPT = """---
name: gateway_test
version: 1
data_class: customer
output_schema: schemas/output.json
sensitive: [customer_name]
---
## System
Return only JSON for TravelOS.
## User
Customer name: {{customer_name}}
Inquiry: {{message}}
"""
VARIABLES = {
    "customer_name": "Asha Synthetic",
    "message": "Email asha@example.test, we want a Goa holiday for four.",
}


def write_prompt(root: Path, *, data_class: str = "customer") -> Path:
    prompts = root / "prompts"
    schemas = prompts / "schemas"
    schemas.mkdir(parents=True, exist_ok=True)
    (schemas / "output.json").write_text(json.dumps(OUTPUT_SCHEMA), encoding="utf-8")
    (prompts / "gateway_test.md").write_text(
        PROMPT.replace("data_class: customer", f"data_class: {data_class}"),
        encoding="utf-8",
    )
    return prompts


def build_gateway(
    root: Path,
    providers: list[LLMProvider],
    *,
    cache: ResponseCache | None = None,
    budget: int | None = None,
    data_class: str = "customer",
    clock: Clock | None = None,
) -> LLMGateway:
    return LLMGateway(
        providers=providers,
        prompts=PromptRegistry(write_prompt(root, data_class=data_class)),
        cache=cache,
        daily_budget_paise=budget,
        clock=clock,
        max_attempts=1,
        backoff_seconds=0.01,
        sleep=lambda _seconds: None,
    )


def call(gateway: LLMGateway, db: Session, **overrides: Any):  # type: ignore[no-untyped-def]
    variables = dict(VARIABLES)
    variables.update(overrides)
    return gateway.complete(db, prompt_name="gateway_test", variables=variables)


def rows(db: Session) -> list[LLMCall]:
    db.flush()
    return list(db.execute(select(LLMCall).order_by(LLMCall.id)).scalars())


def test_customer_data_to_synthetic_only_provider_is_refused(tmp_path: Path, db: Session) -> None:
    provider = FakeProvider(policy=ProviderPolicy(no_training=True, synthetic_only=True))
    gateway = build_gateway(tmp_path, [provider])

    with pytest.raises(LLMPolicyViolationError):
        call(gateway, db)

    calls = rows(db)
    assert len(calls) == 1
    assert calls[0].status == "policy_blocked"
    assert calls[0].provider is None
    assert provider.calls == 0
    task = db.execute(select(HumanTask)).scalar_one()
    assert task.task_type == "llm.policy_blocked"
    assert task.assignee_role == "owner"


def test_a_caller_cannot_downgrade_the_prompt_data_class(tmp_path: Path, db: Session) -> None:
    provider = FakeProvider()
    gateway = build_gateway(tmp_path, [provider])

    with pytest.raises(LLMPolicyViolationError, match="prompt declares customer"):
        gateway.complete(
            db,
            prompt_name="gateway_test",
            variables=VARIABLES,
            data_class=DataClass.SYNTHETIC,
        )

    assert provider.calls == 0
    calls = rows(db)
    assert len(calls) == 1
    assert calls[0].status == "policy_blocked"
    task = db.execute(select(HumanTask)).scalar_one()
    assert "downgrade" in task.title


def test_schema_failure_is_repaired_once_and_cost_is_recorded(tmp_path: Path, db: Session) -> None:
    provider = FakeProvider(
        responses=['{"intent":"telepathy"}', '{"intent":"book","note":"ok"}'],
        pricing=ProviderPricing(
            input_paise_per_million=1_000_000, output_paise_per_million=2_000_000
        ),
    )
    gateway = build_gateway(tmp_path, [provider])

    result = call(gateway, db)

    assert result.parsed == {"intent": "book", "note": "ok"}
    assert result.repairs == 1
    assert provider.calls == 2
    calls = rows(db)
    assert [row.status for row in calls] == ["schema_error", "success"]
    assert result.cost_paise == sum(row.cost_paise for row in calls)
    assert result.cost_paise > 0
    assert result.input_tokens == sum(row.input_tokens for row in calls)
    assert result.output_tokens == sum(row.output_tokens for row in calls)
    assert db.execute(select(AuditLog).where(AuditLog.action == "llm.call_failed")).scalar_one()
    assert db.execute(select(AuditLog).where(AuditLog.action == "llm.call_succeeded")).scalar_one()
    assert db.execute(select(HumanTask)).scalar_one_or_none() is None


def test_two_schema_failures_raise_and_escalate_to_a_human(tmp_path: Path, db: Session) -> None:
    provider = FakeProvider(responses=['{"intent":"admin"}', '{"intent":"admin"}'])
    gateway = build_gateway(tmp_path, [provider])

    with pytest.raises(LLMSchemaError):
        call(gateway, db)

    assert provider.calls == 2
    calls = rows(db)
    assert [row.status for row in calls] == ["schema_error", "schema_error", "schema_error"]
    task = db.execute(select(HumanTask)).scalar_one()
    assert task.task_type == "llm.schema_failure"
    assert task.priority == "high"


def test_auth_failure_falls_back_to_the_next_provider(tmp_path: Path, db: Session) -> None:
    down = FakeProvider(name="down", failures=[AuthProviderError("down", "no key")])
    up = FakeProvider(name="up", responses=['{"intent":"book"}'])
    gateway = build_gateway(tmp_path, [down, up])

    result = call(gateway, db)

    assert result.provider == "up"
    assert down.calls == 1
    assert up.calls == 1
    assert [row.status for row in rows(db)] == ["provider_error", "success"]


def test_all_providers_failing_creates_a_human_task(tmp_path: Path, db: Session) -> None:
    down = FakeProvider(name="down", failures=[AuthProviderError("down", "no key")])
    gateway = build_gateway(tmp_path, [down])

    with pytest.raises(AllProvidersFailedError):
        call(gateway, db)

    calls = rows(db)
    assert calls[-1].status == "all_providers_failed"
    task = db.execute(select(HumanTask)).scalar_one()
    assert task.task_type == "llm.provider_failure"


def test_budget_blocks_the_call_before_a_provider_is_used(tmp_path: Path, db: Session) -> None:
    db.add(
        LLMCall(
            provider="fake",
            model="fake-1",
            prompt_name="gateway_test",
            prompt_version=1,
            data_class="customer",
            status="success",
            cost_paise=10,
            currency="INR",
        )
    )
    db.flush()
    provider = FakeProvider(responses=['{"intent":"book"}'])
    gateway = build_gateway(tmp_path, [provider], budget=5)

    with pytest.raises(LLMBudgetExceededError):
        call(gateway, db)

    assert provider.calls == 0
    assert rows(db)[-1].status == "budget_blocked"
    task = db.execute(select(HumanTask)).scalar_one()
    assert task.task_type == "llm.budget_exceeded"


def test_zero_budget_allows_an_explicitly_free_provider(tmp_path: Path, db: Session) -> None:
    provider = FakeProvider(
        responses=['{"intent":"book"}'],
        pricing=ProviderPricing(free=True),
    )
    gateway = build_gateway(tmp_path, [provider], budget=0)

    result = call(gateway, db)

    assert provider.calls == 1
    assert result.cost_paise == 0
    assert rows(db)[-1].status == "success"


def test_zero_budget_refuses_unpriced_providers(tmp_path: Path, db: Session) -> None:
    provider = FakeProvider(responses=['{"intent":"book"}'])
    gateway = build_gateway(tmp_path, [provider], budget=0)

    with pytest.raises(LLMBudgetExceededError):
        call(gateway, db)

    assert provider.calls == 0
    blocked = rows(db)[-1]
    assert blocked.status == "budget_blocked"
    assert "free providers only" in (blocked.error or "")
    task = db.execute(select(HumanTask)).scalar_one()
    assert task.task_type == "llm.budget_exceeded"


def test_zero_budget_uses_the_free_provider_in_a_mixed_chain(tmp_path: Path, db: Session) -> None:
    paid = FakeProvider(name="paid", responses=['{"intent":"book"}'])
    free = FakeProvider(
        name="free",
        responses=['{"intent":"book"}'],
        pricing=ProviderPricing(free=True),
    )
    gateway = build_gateway(tmp_path, [paid, free], budget=0)

    result = call(gateway, db)

    assert result.provider == "free"
    assert paid.calls == 0
    assert free.calls == 1


def test_budget_day_resets_at_indian_midnight(tmp_path: Path, db: Session) -> None:
    # 19:00 UTC on 24 Oct is 00:30 IST on 25 Oct: the spend belongs to the
    # same Indian accounting day as the call below, so the budget is exhausted.
    db.add(
        LLMCall(
            provider="fake",
            model="fake-1",
            prompt_name="gateway_test",
            prompt_version=1,
            data_class="customer",
            status="success",
            cost_paise=10,
            currency="INR",
            created_at=datetime(2026, 10, 24, 19, 0, tzinfo=UTC),
        )
    )
    db.flush()
    clock = ManualClock(datetime(2026, 10, 25, 5, 0, tzinfo=UTC))
    provider = FakeProvider(responses=['{"intent":"book"}'])
    gateway = build_gateway(tmp_path, [provider], budget=10, clock=clock)

    with pytest.raises(LLMBudgetExceededError):
        call(gateway, db)

    assert provider.calls == 0


def test_cache_hit_avoids_a_second_provider_call(tmp_path: Path, db: Session) -> None:
    provider = FakeProvider(responses=['{"intent":"book"}'])
    gateway = build_gateway(tmp_path, [provider], cache=ResponseCache())

    first = call(gateway, db)
    second = call(gateway, db)

    assert first.cached is False
    assert second.cached is True
    assert provider.calls == 1
    assert second.text == first.text
    calls = rows(db)
    assert sum(1 for row in calls if row.cached) == 1
    assert any(row.cached and row.cost_paise == 0 for row in calls)


def test_pii_is_redacted_before_the_provider_and_restored_in_the_result(
    tmp_path: Path, db: Session
) -> None:
    provider = FakeProvider(
        responses=['{"intent":"book","note":"[[CUSTOMER_NAME_1]] [[EMAIL_1]]"}']
    )
    gateway = build_gateway(tmp_path, [provider])

    result = call(gateway, db)

    sent = provider.requests[0].messages[-1].content
    assert "Asha Synthetic" not in sent
    assert "asha@example.test" not in sent
    assert "[[CUSTOMER_NAME_1]]" in sent
    assert "[[EMAIL_1]]" in sent
    assert result.parsed == {
        "intent": "book",
        "note": "Asha Synthetic asha@example.test",
    }
    stored = rows(db)[-1]
    assert stored.response is not None
    assert "Asha Synthetic" not in json.dumps(stored.response)
    assert "asha@example.test" not in json.dumps(stored.response)
    audit = db.execute(select(AuditLog)).scalars().all()
    assert "asha@example.test" not in json.dumps([entry.after for entry in audit])


def test_llm_calls_orm_update_is_blocked(tmp_path: Path, db: Session) -> None:
    provider = FakeProvider(responses=['{"intent":"book"}'])
    gateway = build_gateway(tmp_path, [provider])
    call(gateway, db)
    row = rows(db)[0]

    row.status = "provider_error"

    with pytest.raises(AppendOnlyError, match="append-only"):
        db.flush()
    db.rollback()


def test_llm_calls_raw_update_is_blocked_by_the_database(tmp_path: Path, db: Session) -> None:
    provider = FakeProvider(responses=['{"intent":"book"}'])
    gateway = build_gateway(tmp_path, [provider])
    call(gateway, db)
    row = rows(db)[0]

    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(
            text("UPDATE llm_calls SET status = 'provider_error' WHERE id = :id"),
            {"id": row.id},
        )
    db.rollback()


def test_adversarial_fixtures_are_rejected_and_escalated(tmp_path: Path, db: Session) -> None:
    fixtures = Path(__file__).resolve().parents[2] / "data" / "eval" / "llm_injection.jsonl"
    cases = [
        json.loads(line)
        for line in fixtures.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]

    for case in cases:
        provider = FakeProvider(responses=[case["compromised_output"], case["compromised_output"]])
        gateway = build_gateway(tmp_path / str(case["id"]), [provider])

        with pytest.raises(LLMSchemaError):
            gateway.complete(
                db,
                prompt_name="gateway_test",
                variables={"customer_name": "Asha Synthetic", "message": case["input"]},
            )

        sent = provider.requests[0].messages[-1].content
        assert sent.count('<<<UNTRUSTED_DATA name="message">>>') == 1, case["id"]
        assert sent.count(UNTRUSTED_CLOSE) == 2, case["id"]
        assert provider.calls == 2, case["id"]

    db.flush()
    calls = rows(db)
    assert calls
    assert {row.status for row in calls} == {"schema_error"}
    tasks = list(db.execute(select(HumanTask)).scalars())
    assert len(tasks) == len(cases)
