"""Provider adapters: fake determinism and HTTP error mapping (no network)."""

from __future__ import annotations

import json

import httpx
import pytest

from travelos.llm.errors import (
    AuthProviderError,
    PermanentProviderError,
    RateLimitedProviderError,
    TransientProviderError,
)
from travelos.llm.providers.anthropic import AnthropicProvider
from travelos.llm.providers.fake import FakeProvider
from travelos.llm.providers.openai_compatible import OpenAICompatibleProvider
from travelos.llm.types import LLMMessage, LLMRequest


def request() -> LLMRequest:
    return LLMRequest(
        messages=(
            LLMMessage(role="system", content="Return JSON."),
            LLMMessage(role="user", content="hello"),
        ),
        temperature=0.1,
        max_tokens=64,
    )


def test_fake_provider_is_deterministic_and_scriptable() -> None:
    provider = FakeProvider(responses=['{"intent":"book"}'])

    first = provider.complete(request())
    second = provider.complete(request())

    assert first.text == '{"intent":"book"}'
    assert second.text == '{"result": "ok"}'
    assert provider.calls == 2
    assert first.input_tokens > 0
    assert first.output_tokens > 0


def test_fake_provider_can_raise_a_scripted_failure() -> None:
    provider = FakeProvider(failures=[AuthProviderError("fake", "no key")])

    with pytest.raises(AuthProviderError):
        provider.complete(request())


def test_openai_success_and_payload_shape() -> None:
    captured: dict[str, object] = {}

    def handler(incoming: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(incoming.content)
        captured["auth"] = incoming.headers.get("authorization")
        return httpx.Response(
            200,
            json={
                "model": "test-model",
                "choices": [{"message": {"content": '{"intent":"book"}'}}],
                "usage": {"prompt_tokens": 12, "completion_tokens": 5},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        base_url="https://llm.example.test/v1",
        api_key="synthetic-key",
        model="test-model",
        client=client,
    )

    response = provider.complete(request())

    assert response.text == '{"intent":"book"}'
    assert response.input_tokens == 12
    assert response.output_tokens == 5
    assert captured["auth"] == "Bearer synthetic-key"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["model"] == "test-model"
    assert body["max_tokens"] == 64
    assert len(body["messages"]) == 2


@pytest.mark.parametrize(
    ("status", "error_type"),
    [
        (429, RateLimitedProviderError),
        (401, AuthProviderError),
        (500, TransientProviderError),
        (400, PermanentProviderError),
    ],
)
def test_openai_status_codes_map_to_typed_errors(status: int, error_type: type[Exception]) -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(status, json={}))
    )
    provider = OpenAICompatibleProvider(
        base_url="https://llm.example.test/v1",
        api_key="synthetic-key",
        model="test-model",
        client=client,
    )

    with pytest.raises(error_type):
        provider.complete(request())


def test_openai_without_a_key_fails_closed() -> None:
    provider = OpenAICompatibleProvider(base_url="https://llm.example.test/v1", model="test-model")

    with pytest.raises(AuthProviderError, match="no API key"):
        provider.complete(request())


def test_openai_timeout_is_transient() -> None:
    def handler(_incoming: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow")

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = OpenAICompatibleProvider(
        base_url="https://llm.example.test/v1",
        api_key="synthetic-key",
        model="test-model",
        client=client,
    )

    with pytest.raises(TransientProviderError, match="timed out"):
        provider.complete(request())


def test_anthropic_success_system_and_headers() -> None:
    captured: dict[str, object] = {}

    def handler(incoming: httpx.Request) -> httpx.Response:
        captured["body"] = json.loads(incoming.content)
        captured["key"] = incoming.headers.get("x-api-key")
        captured["version"] = incoming.headers.get("anthropic-version")
        return httpx.Response(
            200,
            json={
                "model": "claude-test",
                "content": [{"type": "text", "text": '{"intent":"cancel"}'}],
                "usage": {"input_tokens": 9, "output_tokens": 4},
            },
        )

    client = httpx.Client(transport=httpx.MockTransport(handler))
    provider = AnthropicProvider(
        base_url="https://anthropic.example.test",
        api_key="synthetic-key",
        model="claude-test",
        client=client,
    )

    response = provider.complete(request())

    assert response.text == '{"intent":"cancel"}'
    assert response.input_tokens == 9
    assert response.output_tokens == 4
    assert captured["key"] == "synthetic-key"
    assert captured["version"] == "2023-06-01"
    body = captured["body"]
    assert isinstance(body, dict)
    assert body["system"] == "Return JSON."
    assert len(body["messages"]) == 1


def test_anthropic_429_is_rate_limited() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(lambda _request: httpx.Response(429, json={}))
    )
    provider = AnthropicProvider(
        base_url="https://anthropic.example.test",
        api_key="synthetic-key",
        model="claude-test",
        client=client,
    )

    with pytest.raises(RateLimitedProviderError):
        provider.complete(request())


def test_anthropic_malformed_response_is_permanent() -> None:
    client = httpx.Client(
        transport=httpx.MockTransport(
            lambda _request: httpx.Response(200, json={"content": "not-a-list"})
        )
    )
    provider = AnthropicProvider(
        base_url="https://anthropic.example.test",
        api_key="synthetic-key",
        model="claude-test",
        client=client,
    )

    with pytest.raises(PermanentProviderError):
        provider.complete(request())
