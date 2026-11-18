"""Choose RAG_MIN_SIMILARITY from the dev split only (never from the test split).

Runs inside the backend image so it can call the retriever with the threshold switched off:

    docker compose run --rm -T -v "$PWD/evals:/evals:ro" api python /evals/calibrate.py

For every question it records the highest cosine similarity among the top-5 hybrid results.

The threshold is a cheap gate in front of the language model: when no passage reaches it, the
assistant abstains without calling the model. A gate that blocks answerable questions loses
answers for good, while an unanswerable question that passes the gate is still handled by the
model's own abstention and by citation validation. So the rule is recall first: the highest
threshold (0.01 steps) that keeps every answerable dev question, minus a 0.02 safety margin. The
script also reports the best pure separation accuracy, to show how much the two groups overlap.
With `--split test --threshold X` it only reports how a given threshold behaves on test data.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import sys
from pathlib import Path

# The backend package lives in the image's working directory (/app); helpers next to this file.
sys.path[:0] = [os.getcwd(), str(Path(__file__).parent)]

from app.core.config import get_settings  # noqa: E402
from app.core.deps import Principal  # noqa: E402
from app.db.models import Membership, Tenant, User  # noqa: E402
from app.db.session import bind_tenant, session_scope  # noqa: E402
from app.providers.embeddings import get_embedding_provider  # noqa: E402
from app.services.retrieval import retrieve  # noqa: E402
from common import account, load_questions  # noqa: E402
from sqlalchemy import select  # noqa: E402


def principal_for(db, tenant_slug: str, user: str) -> Principal:  # type: ignore[no-untyped-def]
    email, _ = account(tenant_slug, user)
    row = db.execute(
        select(User, Tenant, Membership)
        .join(Membership, Membership.user_id == User.id)
        .join(Tenant, Tenant.id == Membership.tenant_id)
        .where(User.email == email, Tenant.slug == tenant_slug)
    ).one()
    return Principal(row.User.id, row.Tenant.id, row.Membership.role, email)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--split", default="dev", choices=["dev", "test"])
    parser.add_argument("--threshold", type=float, help="threshold to evaluate (test split)")
    args = parser.parse_args()

    settings = get_settings()
    embedder = get_embedding_provider(settings)
    rows = []
    for q in load_questions(args.split):
        if q["category"] in {"isolation", "permission", "injection", "misleading"}:
            continue  # calibrate on plain answerable vs unanswerable questions only
        with session_scope() as db:
            principal = principal_for(db, q["tenant"], q["user"])
            bind_tenant(db, principal.tenant_id)
            result = retrieve(
                db, settings, principal, embedder, q["question"], top_k=5, min_similarity=0.0
            )
        top = max((c.similarity for c in result.chunks), default=0.0)
        rows.append({"id": q["id"], "answerable": q["expect"] == "answer", "top_similarity": top})

    def accuracy(t: float) -> float:
        good = sum((r["top_similarity"] >= t) == r["answerable"] for r in rows)
        return good / len(rows)

    answerable = sorted(r["top_similarity"] for r in rows if r["answerable"])
    unanswerable = sorted(r["top_similarity"] for r in rows if not r["answerable"])
    grid = [round(0.30 + i / 100, 2) for i in range(56)]  # 0.30 .. 0.85
    scores = {t: accuracy(t) for t in grid}
    best = max(scores.values())
    best_range = [t for t in grid if scores[t] == best]
    recall_gate = math.floor((answerable[0] - 0.02) * 100) / 100 if answerable else 0.0
    chosen = args.threshold if args.threshold is not None else recall_gate

    def gate(t: float) -> dict[str, int]:
        return {
            "answerable_kept": sum(s >= t for s in answerable),
            "answerable_blocked": sum(s < t for s in answerable),
            "unanswerable_blocked": sum(s < t for s in unanswerable),
            "unanswerable_passed_to_llm": sum(s >= t for s in unanswerable),
        }

    print(
        json.dumps(
            {
                "split": args.split,
                "embedding_model": embedder.model,
                "questions": len(rows),
                "answerable_top_similarity": {"min": answerable[0], "max": answerable[-1]}
                if answerable
                else None,
                "unanswerable_top_similarity": {"min": unanswerable[0], "max": unanswerable[-1]}
                if unanswerable
                else None,
                "separation": {
                    "best_accuracy": round(best, 4),
                    "best_range": [best_range[0], best_range[-1]],
                    "gate_at_best": gate(best_range[0]),
                },
                "rule": "recall first: lowest answerable top similarity - 0.02, floored to 0.01",
                "chosen_threshold": chosen,
                "gate_at_chosen": gate(chosen),
                "rows": rows,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
