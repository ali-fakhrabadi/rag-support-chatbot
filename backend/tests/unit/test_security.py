from __future__ import annotations

import json
import logging
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pytest

from app.core.config import Settings
from app.core.logging import JsonFormatter, redact
from app.core.passwords import (
    hash_password,
    needs_rehash,
    validate_password_strength,
    verify_password,
)
from app.core.tokens import (
    TokenError,
    create_access_token,
    decode_access_token,
    hash_refresh_token,
    new_refresh_token,
)

SECRET = "s" * 48


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"environment": "test", "jwt_secret": SECRET}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


# --- passwords --------------------------------------------------------------------------------
def test_password_hash_roundtrip() -> None:
    hashed = hash_password("correct horse battery")
    assert hashed.startswith("$argon2id$")
    assert verify_password("correct horse battery", hashed)
    assert not verify_password("wrong password!!", hashed)
    assert not needs_rehash(hashed)


def test_unknown_user_never_verifies() -> None:
    assert not verify_password("anything", None)
    assert not verify_password("anything", "not-a-hash")


@pytest.mark.parametrize("bad", ["short", " leading-space-pw", "trailing-space-pw ", " " * 20])
def test_weak_passwords_are_rejected(bad: str) -> None:
    with pytest.raises(ValueError, match="Password"):
        validate_password_strength(bad)


# --- tokens -----------------------------------------------------------------------------------
def test_access_token_roundtrip() -> None:
    s = settings()
    user, tenant = uuid.uuid4(), uuid.uuid4()
    token, ttl = create_access_token(s, user, tenant)
    claims = decode_access_token(s, token)
    assert (claims.user_id, claims.tenant_id) == (user, tenant)
    assert ttl == 15 * 60


def test_expired_token_is_rejected() -> None:
    s = settings()
    token, _ = create_access_token(
        s, uuid.uuid4(), uuid.uuid4(), now=datetime.now(UTC) - timedelta(hours=2)
    )
    with pytest.raises(TokenError):
        decode_access_token(s, token)


def test_tampered_or_foreign_tokens_are_rejected() -> None:
    s = settings()
    token, _ = create_access_token(s, uuid.uuid4(), uuid.uuid4())
    with pytest.raises(TokenError):
        decode_access_token(settings(jwt_secret="t" * 48), token)
    with pytest.raises(TokenError):
        decode_access_token(settings(jwt_audience="other"), token)
    header, payload, signature = token.split(".")
    with pytest.raises(TokenError):
        decode_access_token(s, f"{header}.{payload}x.{signature}")


def test_alg_none_and_wrong_type_are_rejected() -> None:
    s = settings()
    now = int(datetime.now(UTC).timestamp())
    claims = {
        "sub": str(uuid.uuid4()),
        "tid": str(uuid.uuid4()),
        "typ": "access",
        "iat": now,
        "exp": now + 60,
        "jti": "a",
        "iss": s.jwt_issuer,
        "aud": s.jwt_audience,
    }
    unsigned = jwt.encode(claims, key=None, algorithm="none")  # type: ignore[arg-type]
    with pytest.raises(TokenError):
        decode_access_token(s, unsigned)
    refresh_typed = jwt.encode({**claims, "typ": "refresh"}, SECRET, algorithm="HS256")
    with pytest.raises(TokenError, match="type"):
        decode_access_token(s, refresh_typed)


def test_refresh_tokens_are_random_and_hashed() -> None:
    token, digest = new_refresh_token()
    other, _ = new_refresh_token()
    assert token != other and len(token) >= 40
    assert digest == hash_refresh_token(token) and token not in digest


# --- configuration ----------------------------------------------------------------------------
def test_production_requires_a_long_jwt_secret() -> None:
    with pytest.raises(ValueError, match="RAG_JWT_SECRET"):
        Settings(environment="production", jwt_secret="short")  # type: ignore[arg-type]


def test_openai_provider_requires_a_key() -> None:
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        settings(llm_provider="openai")


def test_overlap_must_be_smaller_than_chunk_size() -> None:
    with pytest.raises(ValueError, match="CHUNK_OVERLAP"):
        settings(chunk_size=500, chunk_overlap=500)


def test_secrets_can_come_from_files(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    secret_file = tmp_path / "jwt"
    secret_file.write_text("f" * 40 + "\n", encoding="utf-8")
    monkeypatch.delenv("RAG_JWT_SECRET", raising=False)
    monkeypatch.setenv("RAG_JWT_SECRET_FILE", str(secret_file))
    monkeypatch.setenv("RAG_ENVIRONMENT", "production")
    assert Settings().jwt_secret.get_secret_value() == "f" * 40


def test_nomic_prefixes_are_applied_automatically() -> None:
    assert settings().doc_prefix == "search_document: "
    assert settings(embedding_model="mxbai-embed-large").query_prefix == ""


def test_embeddings_can_use_a_separate_ollama() -> None:
    assert settings(ollama_base_url="http://gen:11434").embedding_base_url == "http://gen:11434"
    split = settings(
        ollama_base_url="http://gen:11434", ollama_embedding_base_url="http://emb:11434"
    )
    assert split.embedding_base_url == "http://emb:11434"


# --- logging redaction ------------------------------------------------------------------------
def test_redaction_of_tokens_and_keys() -> None:
    text = (
        "Authorization: Bearer abc.def.ghi password=hunter2 api_key: sk-abcdefghijklmnopqrstu "
        "token eyJhbGciOiJIUzI1NiJ9.eyJzdWIiOiIxMjM0NTY3OCJ9.c2lnbmF0dXJlLXZhbHVl"
    )
    out = redact(text)
    for leaked in ("abc.def.ghi", "hunter2", "sk-abcdefghijklmnopqrstu", "eyJhbGciOiJIUzI1NiJ9"):
        assert leaked not in out


def test_json_formatter_redacts_extra_fields() -> None:
    record = logging.LogRecord("t", logging.INFO, __file__, 1, "login password=pw123", None, None)
    record.header = "Bearer secret-token-value"
    data = json.loads(JsonFormatter().format(record))
    assert "pw123" not in data["msg"] and "secret-token-value" not in data["header"]
