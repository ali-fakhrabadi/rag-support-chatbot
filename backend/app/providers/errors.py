"""Provider errors. Messages never contain prompts, document text or credentials."""

from __future__ import annotations

from typing import Literal

import httpx

Kind = Literal[
    "timeout", "unavailable", "rate_limited", "model_missing", "auth", "bad_response", "dimension"
]
RETRYABLE: frozenset[Kind] = frozenset({"timeout", "unavailable", "rate_limited"})


class ProviderError(Exception):
    def __init__(self, provider: str, kind: Kind, message: str):
        super().__init__(f"{provider}: {kind}: {message}")
        self.provider = provider
        self.kind = kind
        self.message = message

    @property
    def retryable(self) -> bool:
        return self.kind in RETRYABLE


def from_http_error(provider: str, exc: Exception) -> ProviderError:
    if isinstance(exc, httpx.TimeoutException):
        return ProviderError(provider, "timeout", "request timed out")
    if isinstance(exc, httpx.TransportError):
        return ProviderError(provider, "unavailable", "cannot reach the provider")
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        body = exc.response.text[:200].lower()
        if status == 429:
            return ProviderError(provider, "rate_limited", "rate limited by the provider")
        if status in (401, 403):
            return ProviderError(provider, "auth", "authentication with the provider failed")
        if status == 404 or "not found" in body:
            return ProviderError(provider, "model_missing", "model not available on the provider")
        if status >= 500:
            return ProviderError(provider, "unavailable", f"provider returned HTTP {status}")
        return ProviderError(provider, "bad_response", f"provider returned HTTP {status}")
    return ProviderError(provider, "bad_response", type(exc).__name__)
