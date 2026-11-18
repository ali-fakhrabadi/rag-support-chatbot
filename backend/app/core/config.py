"""Application settings.

Every value comes from an environment variable with the ``RAG_`` prefix. Secrets can also be
read from a file: ``RAG_JWT_SECRET_FILE=/run/secrets/jwt_secret`` sets ``RAG_JWT_SECRET``.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_SECRET_FIELDS = ("jwt_secret", "db_password", "openai_api_key")


def _read_secret_files(values: dict[str, Any]) -> dict[str, Any]:
    for name in _SECRET_FIELDS:
        path = os.environ.get(f"RAG_{name.upper()}_FILE")
        if path and not values.get(name):
            values[name] = Path(path).read_text(encoding="utf-8").strip()
    return values


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="RAG_", extra="ignore")

    environment: Literal["development", "test", "production"] = "production"
    log_level: str = "INFO"
    enable_docs: bool = True

    # Database: the application connects as a non-owner role so Row-Level Security applies.
    db_host: str = "postgres"
    db_port: int = 5432
    db_name: str = "rag"
    db_user: str = "rag_app"
    db_password: SecretStr = SecretStr("")
    db_pool_size: int = 5

    redis_url: str = "redis://redis:6379/0"

    # Authentication
    jwt_secret: SecretStr = SecretStr("")
    jwt_issuer: str = "rag-support-chatbot"
    jwt_audience: str = "rag-api"
    access_token_minutes: int = Field(15, ge=1, le=120)
    refresh_token_days: int = Field(7, ge=1, le=90)
    trust_proxy_headers: bool = False
    cors_origins: list[str] = []

    # Limits
    max_upload_mb: int = Field(20, ge=1, le=200)
    max_pdf_pages: int = Field(300, ge=1, le=5000)
    max_text_chars: int = Field(2_000_000, ge=1000)
    max_question_chars: int = Field(2000, ge=50, le=10000)
    rate_limit_login_per_minute: int = 10
    rate_limit_chat_per_minute: int = 20
    rate_limit_upload_per_minute: int = 30

    # Chunking
    chunk_size: int = Field(1200, ge=200, le=8000)
    chunk_overlap: int = Field(200, ge=0, le=2000)

    # Embeddings
    embedding_provider: Literal["ollama", "openai", "hashing"] = "ollama"
    embedding_model: str = "nomic-embed-text"
    embedding_dim: int = 768
    embedding_batch_size: int = Field(16, ge=1, le=256)
    embedding_doc_prefix: str | None = None
    embedding_query_prefix: str | None = None
    embed_timeout_s: float = 120.0

    # LLM
    llm_provider: Literal["ollama", "openai"] = "ollama"
    llm_model: str = "llama3.1:8b"
    llm_timeout_s: float = 300.0
    llm_num_ctx: int = 8192
    llm_num_thread: int | None = None
    llm_temperature: float = 0.0
    llm_seed: int = 7
    llm_keep_alive: str = "30m"

    ollama_base_url: str = "http://ollama:11434"
    # Optional separate Ollama for embeddings (for example a small CPU host next to the database
    # while generation runs on a bigger machine). Defaults to ollama_base_url.
    ollama_embedding_base_url: str | None = None
    openai_base_url: str = "https://api.openai.com/v1"
    openai_api_key: SecretStr | None = None

    # Retrieval
    retrieval_top_k: int = Field(5, ge=1, le=20)
    retrieval_candidates: int = Field(20, ge=5, le=200)
    rrf_k: int = 60
    min_similarity: float = Field(0.54, ge=-1.0, le=1.0)
    history_messages: int = Field(4, ge=0, le=20)
    hnsw_ef_search: int = 100

    @model_validator(mode="before")
    @classmethod
    def _load_secret_files(cls, data: Any) -> Any:
        return _read_secret_files(dict(data)) if isinstance(data, dict) else data

    @model_validator(mode="after")
    def _check(self) -> Settings:
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError("RAG_CHUNK_OVERLAP must be smaller than RAG_CHUNK_SIZE")
        if self.environment == "production" and len(self.jwt_secret.get_secret_value()) < 32:
            raise ValueError("RAG_JWT_SECRET must be at least 32 characters in production")
        if "openai" in (self.llm_provider, self.embedding_provider) and not self.openai_api_key:
            raise ValueError("RAG_OPENAI_API_KEY is required when an OpenAI provider is selected")
        return self

    @property
    def database_url(self) -> str:
        from sqlalchemy.engine import URL

        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self.db_password.get_secret_value(),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        ).render_as_string(hide_password=False)

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    @property
    def embedding_base_url(self) -> str:
        return self.ollama_embedding_base_url or self.ollama_base_url

    @property
    def doc_prefix(self) -> str:
        if self.embedding_doc_prefix is not None:
            return self.embedding_doc_prefix
        return "search_document: " if "nomic" in self.embedding_model else ""

    @property
    def query_prefix(self) -> str:
        if self.embedding_query_prefix is not None:
            return self.embedding_query_prefix
        return "search_query: " if "nomic" in self.embedding_model else ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
