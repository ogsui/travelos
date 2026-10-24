"""OpenAI-compatible chat-completions adapter.

This covers the documented ``POST /chat/completions`` shape used by OpenAI,
OpenRouter, OpenCode Zen and local OpenAI-compatible servers. Endpoints are
never guessed: the owner supplies ``LLM_OPENAI_BASE_URL`` per deployment.

No retries, tool calls or streaming here. The gateway owns retries and the
request type has no tools, which keeps an injected message from invoking an
action directly.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import httpx
from pydantic import SecretStr

from travelos.llm.errors import (
    AuthProviderError,
    PermanentProviderError,
    RateLimitedProviderError,
    TransientProviderError,
)
from travelos.llm.types import (
    LLMRequest,
    LLMResponse,
    ProviderPolicy,
    ProviderPricing,
)


class OpenAICompatibleProvider:
    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        api_key: SecretStr | str | None = None,
        name: str = "openai",
        policy: ProviderPolicy | None = None,
        pricing: ProviderPricing | None = None,
        timeout_seconds: float = 30.0,
        client: httpx.Client | None = None,
    ) -> None:
        if not base_url:
            raise ValueError("base_url is required")
        if not model:
            raise ValueError("model is required")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be positive")
        self.name = name
        self.model = model
        self.policy = policy or ProviderPolicy(no_training=True, synthetic_only=False)
        self.pricing = pricing or ProviderPricing()
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout_seconds
        self._client = client

    def complete(self, request: LLMRequest) -> LLMResponse:
        key = _secret(self._api_key)
        if not key:
            raise AuthProviderError(self.name, "no API key configured")
        payload = {
            "model": self.model,
            "messages": [
                {"role": message.role, "content": message.content} for message in request.messages
            ],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
        }
        response = self._post("/chat/completions", payload, key)
        data = _json_object(response, self.name)
        choices = data.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], Mapping):
            raise PermanentProviderError(self.name, "response has no choices")
        message = choices[0].get("message")
        if not isinstance(message, Mapping):
            raise PermanentProviderError(self.name, "response has no message")
        text = _content_text(message.get("content"), self.name)
        usage = data.get("usage")
        input_tokens = 0
        output_tokens = 0
        if isinstance(usage, Mapping):
            input_tokens = _token_count(usage.get("prompt_tokens", usage.get("input_tokens")))
            output_tokens = _token_count(usage.get("completion_tokens", usage.get("output_tokens")))
        model = data.get("model")
        return LLMResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            model=str(model) if isinstance(model, str) and model else self.model,
        )

    def _post(self, path: str, payload: Mapping[str, Any], key: str) -> httpx.Response:
        headers = {
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
        }
        try:
            if self._client is not None:
                response = self._client.post(self._base_url + path, json=payload, headers=headers)
            else:
                with httpx.Client(timeout=self._timeout) as client:
                    response = client.post(self._base_url + path, json=payload, headers=headers)
        except httpx.TimeoutException as exc:
            raise TransientProviderError(self.name, "request timed out") from exc
        except httpx.TransportError as exc:
            raise TransientProviderError(self.name, "transport error") from exc
        _raise_for_status(response, self.name)
        return response


def _raise_for_status(response: httpx.Response, provider: str) -> None:
    status = response.status_code
    if status == 429:
        raise RateLimitedProviderError(provider, "rate limited (HTTP 429)")
    if status in {401, 403}:
        raise AuthProviderError(provider, f"authentication failed (HTTP {status})")
    if status >= 500:
        raise TransientProviderError(provider, f"provider error (HTTP {status})")
    if status >= 400:
        raise PermanentProviderError(provider, f"request rejected (HTTP {status})")


def _json_object(response: httpx.Response, provider: str) -> Mapping[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise PermanentProviderError(provider, "response is not JSON") from exc
    if not isinstance(data, Mapping):
        raise PermanentProviderError(provider, "response is not a JSON object")
    return data


def _content_text(content: object, provider: str) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, Mapping) and isinstance(part.get("text"), str):
                parts.append(part["text"])
        if parts:
            return "".join(parts)
    raise PermanentProviderError(provider, "response content is empty or unsupported")


def _token_count(value: object) -> int:
    if isinstance(value, bool):
        return 0
    if isinstance(value, int):
        return max(0, value)
    return 0


def _secret(value: SecretStr | str | None) -> str:
    if value is None:
        return ""
    if isinstance(value, SecretStr):
        return value.get_secret_value()
    return value
