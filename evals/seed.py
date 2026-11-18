"""Load the evaluation corpus into a running deployment through the public API.

    python evals/seed.py --base-url http://127.0.0.1:8080 --password-file secrets/eval_password

The workspaces and their first admins must exist (evals/seed.sh creates them with the CLI). This
script adds the other users, uploads every document in corpus/manifest.json that is not there
yet, and waits until ingestion has finished. It is safe to run more than once.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from common import MANIFEST, MEDIA_TYPES, Client, multipart

CORPUS = Path(__file__).parent / "corpus"


def seed_tenant(base: str, password: str, tenant: dict, timeout_s: int) -> bool:
    admin = next(u for u in tenant["users"] if u["role"] == "tenant_admin")
    client = Client(base, admin["email"], password, tenant["slug"])

    members = {m["email"] for m in client.call("GET", "/api/v1/tenants/current/members")}
    for user in tenant["users"]:
        if user["email"] not in members:
            client.call(
                "POST",
                "/api/v1/tenants/current/members",
                {
                    "email": user["email"],
                    "full_name": user["full_name"],
                    "password": password,
                    "role": user["role"],
                },
            )
            print(f"[{tenant['slug']}] added {user['email']} ({user['role']})")

    existing = {
        d["filename"]: d for d in client.call("GET", "/api/v1/documents?limit=100")["items"]
    }
    for doc in tenant["documents"]:
        path = CORPUS / doc["file"]
        if path.name in existing:
            continue
        body, content_type = multipart(
            {"title": doc["title"], "access_level": doc.get("access_level", "all_members")},
            path.name,
            path.read_bytes(),
            MEDIA_TYPES[path.suffix],
        )
        client.call("POST", "/api/v1/documents", raw=body, content_type=content_type)
        print(f"[{tenant['slug']}] uploaded {path.name}")

    deadline = time.monotonic() + timeout_s
    while True:
        docs = client.call("GET", "/api/v1/documents?limit=100")["items"]
        pending = [d for d in docs if d["status"] in ("pending", "processing")]
        if not pending or time.monotonic() > deadline:
            break
        time.sleep(5)
    failed = [d for d in docs if d["status"] == "failed"]
    for d in docs:
        print(
            f"[{tenant['slug']}] {d['status']:<10} {d['chunk_count']:>3} chunks  {d['filename']}"
            + (f"  ({d['error']})" if d["error"] else "")
        )
    return not pending and not failed


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--password-file", required=True)
    parser.add_argument("--timeout", type=int, default=1800, help="seconds to wait for ingestion")
    args = parser.parse_args()
    password = Path(args.password_file).read_text(encoding="utf-8").strip()
    started = time.monotonic()
    ok = all(seed_tenant(args.base_url, password, t, args.timeout) for t in MANIFEST["tenants"])
    print(f"seeded in {time.monotonic() - started:.0f} s")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
