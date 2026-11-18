"""Provider error handling with a fake HTTP transport (isolated unit tests).

Real calls to Ollama are covered by the end-to-end tests and the evaluation.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable

import httpx
import pytest

from app.core.config import Settings
from app.providers.embeddings import HashingEmbeddings, OllamaEmbeddings, OpenAIEmbeddings
from app.providers.errors import ProviderError
from app.providers.llm import ChatTurn, OllamaLLM, OpenAILLM
from app.rag.prompt import ANSWER_SCHEMA


def settings(**overrides: object) -> Settings:
    base = {
        "environment": "test",
        "jwt_secret": "x" * 40,
        "embedding_dim": 4,
        "embedding_batch_size": 2,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def client(handler: Callable[[httpx.Request], httpx.Response]) -> httpx.Client:
    return httpx.Client(base_url="http://model", transport=httpx.MockTransport(handler))


def test_ollama_embeddings_batches_and_prefixes() -> None:
    seen: list[dict[str, object]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        seen.append(body)
        return httpx.Response(200, json={"embeddings": [[0.1, 0.2, 0.3, 0.4]] * len(body["input"])})

    provider = OllamaEmbeddings(settings(), client(handler))
    vectors = provider.embed_documents(["a", "b", "c"])
    assert len(vectors) == 3
    assert [len(b["input"]) for b in seen] == [2, 1]  # type: ignore[arg-type]
    assert seen[0]["input"] == ["search_document: a", "search_document: b"]
    provider.embed_query("q")
    assert seen[-1]["input"] == ["search_query: q"]


def test_embedding_dimension_mismatch_is_rejected() -> None:
    provider = OllamaEmbeddings(
        settings(), client(lambda r: httpx.Response(200, json={"embeddings": [[0.1, 0.2]]}))
    )
    with pytest.raises(ProviderError) as info:
        provider.embed_query("q")
    assert info.value.kind == "dimension" and not info.value.retryable


@pytest.mark.parametrize(
    ("response", "kind", "retryable"),
    [
        (httpx.Response(404, json={"error": "model 'x' not found"}), "model_missing", False),
        (httpx.Response(429), "rate_limited", True),
        (httpx.Response(503), "unavailable", True),
        (httpx.Response(401), "auth", False),
        (httpx.Response(200, json={"nope": 1}), "bad_response", False),
    ],
)
def test_embedding_http_errors_are_classified(
    response: httpx.Response, kind: str, retryable: bool
) -> None:
    provider = OllamaEmbeddings(settings(), client(lambda r: response))
    with pytest.raises(ProviderError) as info:
        provider.embed_query("q")
    assert (info.value.kind, info.value.retryable) == (kind, retryable)


def test_timeouts_and_connection_errors_are_retryable() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    for handler, kind in ((timeout, "timeout"), (refused, "unavailable")):
        with pytest.raises(ProviderError) as info:
            OllamaEmbeddings(settings(), client(handler)).embed_query("q")
        assert info.value.kind == kind and info.value.retryable


def test_openai_embeddings_request_shape() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert request.url.path.endswith("/embeddings")
        assert body["dimensions"] == 4
        data = [
            {"index": i, "embedding": [float(i)] * 4} for i in reversed(range(len(body["input"])))
        ]
        return httpx.Response(200, json={"data": data})

    s = settings(
        embedding_provider="openai",
        openai_api_key="sk-test-key-not-real",
        embedding_model="text-embedding-3-small",
    )
    vectors = OpenAIEmbeddings(s, client(handler)).embed_documents(["a", "b"])
    assert vectors == [[0.0] * 4, [1.0] * 4]  # re-ordered by index


def test_hashing_embeddings_are_deterministic_and_normalised() -> None:
    provider = HashingEmbeddings(settings(embedding_dim=64))
    a, b = provider.embed_query("Expense reports"), provider.embed_query("Expense reports")
    assert a == b
    assert math.isclose(sum(v * v for v in a), 1.0, rel_tol=1e-9)
    assert provider.embed_query("") == [0.0] * 64


def _ollama_chat(content: str) -> Callable[[httpx.Request], httpx.Response]:
    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        assert body["format"] == ANSWER_SCHEMA and body["stream"] is False
        assert body["options"]["temperature"] == 0.0
        return httpx.Response(
            200,
            json={
                "model": "m",
                "message": {"content": content},
                "prompt_eval_count": 10,
                "eval_count": 5,
            },
        )

    return handler


def test_ollama_llm_parses_json() -> None:
    llm = OllamaLLM(
        settings(),
        client(_ollama_chat('{"answer": "x", "citations": [], "insufficient_evidence": true}')),
    )
    result = llm.generate_json([ChatTurn("user", "q")], ANSWER_SCHEMA)
    assert result.data["insufficient_evidence"] is True
    assert result.prompt_tokens == 10


@pytest.mark.parametrize("content", ["not json", "[1, 2]"])
def test_ollama_llm_rejects_invalid_json(content: str) -> None:
    llm = OllamaLLM(settings(), client(_ollama_chat(content)))
    with pytest.raises(ProviderError) as info:
        llm.generate_json([ChatTurn("user", "q")], ANSWER_SCHEMA)
    assert info.value.kind == "bad_response"


def test_openai_llm_uses_strict_json_schema_and_maps_auth_errors() -> None:
    captured: dict[str, object] = {}

    def ok(request: httpx.Request) -> httpx.Response:
        captured.update(json.loads(request.content))
        content = '{"answer": "a [S1]", "citations": ["S1"], "insufficient_evidence": false}'
        return httpx.Response(
            200,
            json={
                "model": "gpt",
                "choices": [{"message": {"content": content}}],
                "usage": {"prompt_tokens": 3, "completion_tokens": 2},
            },
        )

    s = settings(llm_provider="openai", openai_api_key="sk-test-key-not-real")
    result = OpenAILLM(s, client(ok)).generate_json([ChatTurn("user", "q")], ANSWER_SCHEMA)
    assert result.data["citations"] == ["S1"]
    fmt = captured["response_format"]
    assert isinstance(fmt, dict) and fmt["json_schema"]["strict"] is True

    with pytest.raises(ProviderError) as info:
        OpenAILLM(s, client(lambda r: httpx.Response(401))).generate_json([], ANSWER_SCHEMA)
    assert info.value.kind == "auth"
    assert "sk-test" not in str(info.value)
