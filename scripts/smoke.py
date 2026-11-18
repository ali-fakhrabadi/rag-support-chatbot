"""End-to-end smoke test against a running deployment (standard library only).

    python scripts/smoke.py --base-url http://127.0.0.1:8080 --email admin@x.example \
        --workspace demo --password-file secrets/demo_password

Uploads a small Markdown file, waits for ingestion, asks one answerable and one unanswerable
question, prints the answers and citations, then deletes the document.
"""

from __future__ import annotations

import argparse
import json
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path
from typing import Any

DOC = """# Smoke test policy {marker}

## Expense approvals
Managers must approve expense reports within 9 business days of submission.

## Equipment
Each engineer receives one laptop every 36 months.
"""


def call(base: str, method: str, path: str, token: str | None = None,
         body: dict[str, Any] | None = None, raw: bytes | None = None,
         content_type: str = "application/json") -> tuple[int, Any]:
    headers = {"Content-Type": content_type}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = raw if raw is not None else (json.dumps(body).encode() if body is not None else None)
    request = urllib.request.Request(base + path, data=data, method=method, headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=600) as response:  # noqa: S310
            text = response.read().decode()
            return response.status, json.loads(text) if text else None
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read().decode() or "null")


def multipart(filename: str, content: bytes, fields: dict[str, str]) -> tuple[bytes, str]:
    boundary = uuid.uuid4().hex
    parts = []
    for name, value in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n'
                     f"{value}\r\n".encode())
    parts.append(
        f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="{filename}"\r\n'
        f"Content-Type: text/markdown\r\n\r\n".encode() + content + b"\r\n"
    )
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--email", required=True)
    parser.add_argument("--workspace", required=True)
    parser.add_argument("--password-file", required=True)
    args = parser.parse_args()
    base = args.base_url.rstrip("/")
    password = Path(args.password_file).read_text(encoding="utf-8").strip()

    status, tokens = call(base, "POST", "/api/v1/auth/login", body={
        "email": args.email, "password": password, "workspace": args.workspace})
    assert status == 200, (status, tokens)
    token = tokens["access_token"]

    marker = uuid.uuid4().hex[:8]
    body, ctype = multipart(f"smoke-{marker}.md", DOC.format(marker=marker).encode(), {})
    status, upload = call(base, "POST", "/api/v1/documents", token, raw=body, content_type=ctype)
    assert status == 202, (status, upload)
    doc_id, job_id = upload["document"]["id"], upload["job"]["id"]
    started = time.time()
    while True:
        _, job = call(base, "GET", f"/api/v1/ingestion-jobs/{job_id}", token)
        if job["status"] in ("succeeded", "failed"):
            break
        time.sleep(1)
    print(f"ingestion {job['status']} in {time.time() - started:.1f}s", job.get("error") or "")
    assert job["status"] == "succeeded"

    for question in ("How many business days do managers have to approve expense reports?",
                     "What is the parking policy for visitors?"):
        started = time.time()
        status, answer = call(base, "POST", "/api/v1/chat", token, body={"question": question})
        assert status == 200, (status, answer)
        a = answer["answer"]
        print(f"\nQ: {question}\nA ({time.time() - started:.1f}s, abstained={a['abstained']}): "
              f"{a['content']}")
        for c in a["citations"]:
            print(f"   [{c['source_id']}] {c['document_title']} / {c['section']}")

    status, _ = call(base, "DELETE", f"/api/v1/documents/{doc_id}", token)
    assert status == 204
    print("\nsmoke test passed")


if __name__ == "__main__":
    main()
