"""LLM providers returning schema-constrained JSON.

* ``ollama`` (default): local model, structured output through the ``format`` JSON schema.
* ``openai`` (optional): prompts, including retrieved document passages, are sent to OpenAI.

The model has no tools: it receives text and returns JSON. Nothing it says is executed.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Protocol

import httpx

from app.core.config import Settings
from app.providers.errors import ProviderError, from_http_error


@dataclass(frozen=True)
class ChatTurn:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass
class LLMResult:
    data: dict[str, Any]
    model: str
    duration_ms: int
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class LLMProvider(Protocol):
    model: str

    def generate_json(self, messages: list[ChatTurn], schema: dict[str, Any]) -> LLMResult: ...


def _parse_json(provider: str, text: str) -> dict[str, Any]:
    try:
        data = json.loads(text)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ProviderError(provider, "bad_response", "model did not return valid JSON") from exc
    if not isinstance(data, dict):
        raise ProviderError(provider, "bad_response", "model returned JSON that is not an object")
    return data


class OllamaLLM:
    name = "ollama"

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.model = settings.llm_model
        self._options: dict[str, Any] = {
            "temperature": settings.llm_temperature,
            "seed": settings.llm_seed,
            "num_ctx": settings.llm_num_ctx,
        }
        if settings.llm_num_thread:
            self._options["num_thread"] = settings.llm_num_thread
        self._keep_alive = settings.llm_keep_alive
        self._client = client or httpx.Client(
            base_url=settings.ollama_base_url, timeout=settings.llm_timeout_s
        )

    def generate_json(self, messages: list[ChatTurn], schema: dict[str, Any]) -> LLMResult:
        started = time.perf_counter()
        try:
            response = self._client.post(
                "/api/chat",
                json={
                    "model": self.model,
                    "messages": [{"role": m.role, "content": m.content} for m in messages],
                    "stream": False,
                    "format": schema,
                    "options": self._options,
                    "keep_alive": self._keep_alive,
                },
            )
            response.raise_for_status()
            body = response.json()
            content = body["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
            raise from_http_error(self.name, exc) from exc
        return LLMResult(
            data=_parse_json(self.name, content),
            model=str(body.get("model", self.model)),
            duration_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=body.get("prompt_eval_count"),
            completion_tokens=body.get("eval_count"),
            extra={
                "load_ms": int(body.get("load_duration", 0) / 1e6),
                "prompt_eval_ms": int(body.get("prompt_eval_duration", 0) / 1e6),
                "eval_ms": int(body.get("eval_duration", 0) / 1e6),
            },
        )


class OpenAILLM:
    name = "openai"

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.model = settings.llm_model
        self._temperature = settings.llm_temperature
        self._seed = settings.llm_seed
        key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
        self._client = client or httpx.Client(
            base_url=settings.openai_base_url,
            timeout=settings.llm_timeout_s,
            headers={"Authorization": f"Bearer {key}"},
        )

    def generate_json(self, messages: list[ChatTurn], schema: dict[str, Any]) -> LLMResult:
        started = time.perf_counter()
        try:
            response = self._client.post(
                "/chat/completions",
                json={
                    "model": self.model,
                    "messages": [{"role": m.role, "content": m.content} for m in messages],
                    "temperature": self._temperature,
                    "seed": self._seed,
                    "response_format": {
                        "type": "json_schema",
                        "json_schema": {
                            "name": "grounded_answer",
                            "schema": schema,
                            "strict": True,
                        },
                    },
                },
            )
            response.raise_for_status()
            body = response.json()
            content = body["choices"][0]["message"]["content"]
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError) as exc:
            raise from_http_error(self.name, exc) from exc
        usage = body.get("usage") or {}
        return LLMResult(
            data=_parse_json(self.name, content),
            model=str(body.get("model", self.model)),
            duration_ms=int((time.perf_counter() - started) * 1000),
            prompt_tokens=usage.get("prompt_tokens"),
            completion_tokens=usage.get("completion_tokens"),
        )


def get_llm_provider(settings: Settings) -> LLMProvider:
    if settings.llm_provider == "openai":
        return OpenAILLM(settings)
    return OllamaLLM(settings)
