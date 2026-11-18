"""Shared helpers for the evaluation scripts (standard library only)."""

from __future__ import annotations

import json
import re
import time
import unicodedata
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

HERE = Path(__file__).parent
MANIFEST = json.loads((HERE / "corpus" / "manifest.json").read_text(encoding="utf-8"))
MEDIA_TYPES = {".pdf": "application/pdf", ".md": "text/markdown", ".txt": "text/plain"}
_DASHES = dict.fromkeys(
    map(ord, chr(0x2010) + chr(0x2011) + chr(0x2012) + chr(0x2013) + chr(0x2014)), "-"
)


def load_questions(split: str) -> list[dict[str, Any]]:
    lines = (HERE / "questions.jsonl").read_text(encoding="utf-8").splitlines()
    questions = [json.loads(line) for line in lines if line.strip()]
    return [q for q in questions if split == "all" or q["split"] == split]


def norm(text: str) -> str:
    """Normalise text for deterministic matching: case, dashes, thousands separators, times."""
    text = unicodedata.normalize("NFKC", text).lower().translate(_DASHES)
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    text = re.sub(r"(\d)(?::00)?\s*a\.?\s?m\.?(?![a-z])", r"\1am", text)
    text = re.sub(r"(\d)(?::00)?\s*p\.?\s?m\.?(?![a-z])", r"\1pm", text)
    return re.sub(r"\s+", " ", text).strip()


def account(tenant: str, user: str) -> tuple[str, str]:
    """(email, workspace slug) of the evaluation account for a tenant and role."""
    role = "tenant_admin" if user == "admin" else "member"
    for t in MANIFEST["tenants"]:
        if t["slug"] == tenant:
            for u in t["users"]:
                if u["role"] == role:
                    return u["email"], tenant
    raise KeyError((tenant, user))


class ApiError(RuntimeError):
    def __init__(self, status: int, body: Any) -> None:
        super().__init__(f"HTTP {status}: {body}")
        self.status = status
        self.body = body


class Client:
    """Minimal API client that signs in again when the short-lived access token expires."""

    def __init__(self, base_url: str, email: str, password: str, workspace: str) -> None:
        self.base = base_url.rstrip("/")
        self.email, self.password, self.workspace = email, password, workspace
        self.token: str | None = None

    def _send(
        self, method: str, path: str, data: bytes | None, content_type: str | None, timeout: float
    ) -> tuple[int, Any]:
        headers = {"Accept": "application/json"}
        if content_type:
            headers["Content-Type"] = content_type
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(  # noqa: S310 - http(s) base URL from the operator
            self.base + path, data=data, method=method, headers=headers
        )
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310
                raw = response.read().decode()
                return response.status, json.loads(raw) if raw else None
        except urllib.error.HTTPError as exc:
            raw = exc.read().decode()
            try:
                return exc.code, json.loads(raw) if raw else None
            except json.JSONDecodeError:
                return exc.code, raw

    def login(self) -> None:
        self.token = None
        body = {"email": self.email, "password": self.password, "workspace": self.workspace}
        status, data = self._send(
            "POST", "/api/v1/auth/login", json.dumps(body).encode(), "application/json", 30
        )
        if status != 200:
            raise ApiError(status, data)
        self.token = data["access_token"]

    def call(
        self,
        method: str,
        path: str,
        body: Any = None,
        *,
        raw: bytes | None = None,
        content_type: str | None = "application/json",
        timeout: float = 600,
        ok: tuple[int, ...] = (200, 201, 202, 204),
    ) -> Any:
        if self.token is None:
            self.login()
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
        for attempt in range(4):
            status, result = self._send(method, path, data, content_type if data else None, timeout)
            if status == 401 and attempt == 0:
                self.login()
                continue
            if status == 429:  # rate limited: wait for the next window
                time.sleep(15)
                continue
            if status not in ok:
                raise ApiError(status, result)
            return result
        raise ApiError(status, result)


def multipart(
    fields: dict[str, str], filename: str, content: bytes, media_type: str
) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = [
        f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode()
        for k, v in fields.items()
    ]
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: {media_type}\r\n\r\n".encode()
        + content
        + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def percentile(values: list[float], q: float) -> float | None:
    """Nearest-rank percentile (q in 0..100)."""
    if not values:
        return None
    ordered = sorted(values)
    rank = max(1, -(-len(ordered) * q // 100))
    return ordered[int(rank) - 1]
