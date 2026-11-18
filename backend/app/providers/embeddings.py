"""Embedding providers.

* ``ollama`` (default): a local model such as ``nomic-embed-text``. Text never leaves your network.
* ``openai`` (optional): document chunks and questions are sent to OpenAI. Not private from
  third parties; see docs/security.md.
* ``hashing``: a deterministic feature-hashing embedding with no model. It exists so CI and
  development can exercise the real database and retrieval code without a model server. It
  matches words, not meaning, and must not be used to judge answer quality.

The vector dimension is checked on every call; vectors of another size are rejected instead of
being stored next to incompatible ones.
"""

from __future__ import annotations

import hashlib
import itertools
import math
import re
from typing import Protocol

import httpx

from app.core.config import Settings
from app.providers.errors import ProviderError, from_http_error


class EmbeddingProvider(Protocol):
    model: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def _check(provider: str, vectors: list[list[float]], expected: int, dim: int) -> None:
    if len(vectors) != expected:
        raise ProviderError(provider, "bad_response", "wrong number of embeddings returned")
    for vector in vectors:
        if len(vector) != dim:
            raise ProviderError(
                provider,
                "dimension",
                f"model returned {len(vector)} dimensions, the index expects {dim}",
            )


class OllamaEmbeddings:
    name = "ollama"

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.model = settings.embedding_model
        self.dim = settings.embedding_dim
        self._doc_prefix = settings.doc_prefix
        self._query_prefix = settings.query_prefix
        self._batch = settings.embedding_batch_size
        self._keep_alive = settings.llm_keep_alive
        self._client = client or httpx.Client(
            base_url=settings.ollama_base_url, timeout=settings.embed_timeout_s
        )

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(inputs), self._batch):
            batch = inputs[start : start + self._batch]
            try:
                response = self._client.post(
                    "/api/embed",
                    json={
                        "model": self.model,
                        "input": batch,
                        "truncate": True,
                        "keep_alive": self._keep_alive,
                    },
                )
                response.raise_for_status()
                data = response.json()
            except (httpx.HTTPError, ValueError) as exc:
                raise from_http_error(self.name, exc) from exc
            batch_vectors = data.get("embeddings") if isinstance(data, dict) else None
            if not isinstance(batch_vectors, list):
                raise ProviderError(self.name, "bad_response", "no embeddings in response")
            _check(self.name, batch_vectors, len(batch), self.dim)
            vectors.extend(batch_vectors)
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed([self._doc_prefix + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        return self._embed([self._query_prefix + text])[0]


class OpenAIEmbeddings:
    name = "openai"

    def __init__(self, settings: Settings, client: httpx.Client | None = None):
        self.model = settings.embedding_model
        self.dim = settings.embedding_dim
        self._batch = settings.embedding_batch_size
        key = settings.openai_api_key.get_secret_value() if settings.openai_api_key else ""
        self._client = client or httpx.Client(
            base_url=settings.openai_base_url,
            timeout=settings.embed_timeout_s,
            headers={"Authorization": f"Bearer {key}"},
        )

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for start in range(0, len(inputs), self._batch):
            batch = inputs[start : start + self._batch]
            try:
                response = self._client.post(
                    "/embeddings",
                    # text-embedding-3 models can return a shorter vector to fit the index.
                    json={"model": self.model, "input": batch, "dimensions": self.dim},
                )
                response.raise_for_status()
                data = response.json()["data"]
            except (httpx.HTTPError, ValueError, KeyError, TypeError) as exc:
                raise from_http_error(self.name, exc) from exc
            batch_vectors = [item["embedding"] for item in sorted(data, key=lambda i: i["index"])]
            _check(self.name, batch_vectors, len(batch), self.dim)
            vectors.extend(batch_vectors)
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts)

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


_TOKEN = re.compile(r"[a-z0-9]+")


class HashingEmbeddings:
    """Deterministic bag-of-words embedding (unigrams + bigrams, signed hashing, L2 norm)."""

    name = "hashing"

    def __init__(self, settings: Settings):
        self.model = "hashing-v1"
        self.dim = settings.embedding_dim

    def _vector(self, text: str) -> list[float]:
        tokens = _TOKEN.findall(text.lower())
        features = tokens + [f"{a} {b}" for a, b in itertools.pairwise(tokens)]
        vector = [0.0] * self.dim
        for feature in features:
            digest = hashlib.blake2b(feature.encode(), digest_size=8).digest()
            index = int.from_bytes(digest[:4], "big") % self.dim
            vector[index] += 1.0 if digest[4] & 1 else -1.0
        norm = math.sqrt(sum(v * v for v in vector)) or 1.0
        return [v / norm for v in vector]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


def get_embedding_provider(settings: Settings) -> EmbeddingProvider:
    if settings.embedding_provider == "ollama":
        return OllamaEmbeddings(settings)
    if settings.embedding_provider == "openai":
        return OpenAIEmbeddings(settings)
    return HashingEmbeddings(settings)
