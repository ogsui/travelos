"""Cost arithmetic, cache expiry and circuit-breaker behaviour."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from travelos.core.clock import ManualClock
from travelos.llm.cache import CachedResponse, ResponseCache
from travelos.llm.circuit import CircuitBreaker
from travelos.llm.gateway import accounting_day_start
from travelos.llm.types import DataClass, ProviderPolicy, ProviderPricing


def test_cost_is_integer_paise_and_rounds_up() -> None:
    pricing = ProviderPricing(input_paise_per_million=1_000, output_paise_per_million=2_000)

    assert pricing.cost_paise(1_000_000, 1_000_000) == 3_000
    assert pricing.cost_paise(0, 0) == 0
    assert pricing.cost_paise(1, 0) == 1  # 0.001 paise rounds up to 1 paise


def test_negative_pricing_is_rejected() -> None:
    with pytest.raises(ValueError):
        ProviderPricing(input_paise_per_million=-1)


def test_free_pricing_must_actually_be_zero() -> None:
    assert ProviderPricing(free=True).cost_paise(1_000_000, 1_000_000) == 0

    with pytest.raises(ValueError, match="free provider"):
        ProviderPricing(input_paise_per_million=1, free=True)


def test_accounting_day_starts_at_indian_midnight() -> None:
    kolkata = ZoneInfo("Asia/Kolkata")
    # 20:00 UTC on 24 Oct is 01:30 IST on 25 Oct, so the Indian day began at
    # 18:30 UTC on 24 Oct.
    start = accounting_day_start(datetime(2026, 10, 24, 20, 0, tzinfo=UTC), kolkata)

    assert start == datetime(2026, 10, 24, 18, 30, tzinfo=UTC)

    # 18:29 UTC is still 23:59 IST on the previous Indian day.
    before = accounting_day_start(datetime(2026, 10, 24, 18, 29, tzinfo=UTC), kolkata)
    assert before == datetime(2026, 10, 23, 18, 30, tzinfo=UTC)


def test_policy_only_allows_synthetic_on_a_training_provider() -> None:
    training_allowed = ProviderPolicy(no_training=False, synthetic_only=False)
    synthetic_only = ProviderPolicy(no_training=True, synthetic_only=True)
    safe = ProviderPolicy(no_training=True, synthetic_only=False)

    assert training_allowed.accepts(DataClass.SYNTHETIC)
    assert not training_allowed.accepts(DataClass.CUSTOMER)
    assert not training_allowed.accepts(DataClass.PUBLIC)
    assert synthetic_only.accepts(DataClass.SYNTHETIC)
    assert not synthetic_only.accepts(DataClass.CUSTOMER)
    assert safe.accepts(DataClass.CUSTOMER)
    assert safe.accepts(DataClass.PUBLIC)


def test_cache_hit_and_expiry() -> None:
    clock = ManualClock(datetime(2026, 10, 24, 10, 0, tzinfo=UTC))
    cache = ResponseCache(ttl=timedelta(minutes=5), clock=clock)
    entry = CachedResponse(
        provider="fake",
        model="fake-1",
        text='{"ok":true}',
        parsed={"ok": True},
        input_tokens=3,
        output_tokens=2,
        repairs=0,
    )

    key = cache.key("prompt", 1, "hello")
    cache.put(key, entry)

    assert cache.get(key) == entry
    clock.advance(timedelta(minutes=6))
    assert cache.get(key) is None


def test_cache_is_bounded() -> None:
    cache = ResponseCache(max_entries=2)
    entry = CachedResponse(
        provider="fake",
        model="fake-1",
        text="x",
        parsed=None,
        input_tokens=0,
        output_tokens=0,
        repairs=0,
    )
    for key in ("a", "b", "c"):
        cache.put(key, entry)

    assert len(cache) == 2
    assert cache.get("a") is None


def test_circuit_opens_after_threshold_and_half_opens_later() -> None:
    clock = ManualClock(datetime(2026, 10, 24, 10, 0, tzinfo=UTC))
    breaker = CircuitBreaker(failure_threshold=2, reset_after=timedelta(seconds=10), clock=clock)

    assert breaker.allows()
    breaker.record_failure()
    assert breaker.allows()
    breaker.record_failure()
    assert not breaker.allows()
    assert breaker.state == "open"

    clock.advance(timedelta(seconds=11))

    assert breaker.state == "half_open"
    assert breaker.allows()
    breaker.record_success()
    assert breaker.state == "closed"


def test_circuit_rejects_invalid_configuration() -> None:
    with pytest.raises(ValueError):
        CircuitBreaker(failure_threshold=0)
    with pytest.raises(ValueError):
        CircuitBreaker(reset_after=timedelta(0))


def test_llm_request_has_no_tool_or_function_field() -> None:
    from travelos.llm.types import LLMMessage, LLMRequest

    request = LLMRequest(messages=(LLMMessage(role="user", content="hello"),))

    assert set(request.__dataclass_fields__) == {"messages", "temperature", "max_tokens"}
