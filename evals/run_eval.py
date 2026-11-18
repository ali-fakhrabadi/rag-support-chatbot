"""Run the labeled questions against a running deployment and score them deterministically.

    python evals/run_eval.py --base-url http://127.0.0.1:8080 \
        --password-file secrets/eval_password --split test --out evals/out/test.json

For every question the script calls `POST /api/v1/search` (top 5, the same retriever and
threshold the chat endpoint uses) and `POST /api/v1/chat` in a fresh conversation, then fetches
each cited passage. Scoring uses only string rules from questions.jsonl, no model-as-judge:

- Recall@5: share of gold documents that appear in the top-5 results.
- Evidence@5: share of gold evidence sentences contained in a top-5 passage of the right document.
- Answer correctness: the answer is not an abstention, contains every required fact (any listed
  alternative counts) and none of the forbidden strings.
- Citation accuracy: share of citations pointing to a gold document; "evidence cited" means at
  least one cited passage contains the gold evidence.
- Abstention accuracy: abstain when expected, answer when expected.
- Isolation and permissions: citations to another workspace's documents, or to admin-only
  documents for a member, are counted as leaks.
"""

from __future__ import annotations

import argparse
import json
import platform
import statistics
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from common import MANIFEST, Client, account, load_questions, norm, percentile


def scope_documents(clients: dict[tuple[str, str], Client]) -> dict[str, dict[str, dict[str, Any]]]:
    """Document id -> {file, access_level} for every workspace, read with its admin account."""
    out: dict[str, dict[str, dict[str, Any]]] = {}
    for tenant in MANIFEST["tenants"]:
        by_name = {Path(d["file"]).name: d for d in tenant["documents"]}
        docs = clients[(tenant["slug"], "admin")].call("GET", "/api/v1/documents?limit=100")[
            "items"
        ]
        out[tenant["slug"]] = {
            d["id"]: {
                "file": by_name[d["filename"]]["file"]
                if d["filename"] in by_name
                else d["filename"],
                "access_level": d["access_level"],
                "status": d["status"],
            }
            for d in docs
        }
    return out


def evaluate(
    question: dict[str, Any],
    client: Client,
    docs: dict[str, dict[str, dict[str, Any]]],
    chunk_cache: dict[str, str],
) -> dict[str, Any]:
    all_docs = {doc_id: (slug, info) for slug, d in docs.items() for doc_id, info in d.items()}

    started = time.perf_counter()
    search = client.call("POST", "/api/v1/search", {"query": question["question"], "top_k": 5})
    search_ms = (time.perf_counter() - started) * 1000
    hits = search["hits"]
    retrieved = [
        {
            "file": all_docs.get(h["document_id"], ("?", {"file": h["document_title"]}))[1]["file"],
            "similarity": h["similarity"],
            "content": h["content"],
        }
        for h in hits
    ]

    started = time.perf_counter()
    chat = client.call("POST", "/api/v1/chat", {"question": question["question"]}, timeout=900)
    chat_ms = (time.perf_counter() - started) * 1000
    answer = chat["answer"]
    meta = answer.get("meta") or {}

    citations = []
    for c in answer["citations"]:
        if c["chunk_id"] not in chunk_cache:
            chunk = client.call(
                "GET", f"/api/v1/documents/{c['document_id']}/chunks/{c['chunk_id']}"
            )
            chunk_cache[c["chunk_id"]] = chunk["content"]
        owner, info = all_docs.get(c["document_id"], ("?", {"file": "?", "access_level": "?"}))
        citations.append(
            {
                "source_id": c["source_id"],
                "file": info["file"],
                "tenant": owner,
                "access_level": info["access_level"],
                "content": chunk_cache[c["chunk_id"]],
            }
        )

    text = norm(answer["content"])
    must_ok = all(any(norm(alt) in text for alt in group) for group in question["must_include"])
    forbidden = [s for s in question["must_not_include"] if norm(s) in text]
    abstained = bool(answer["abstained"])
    expect = question["expect"]
    if expect == "answer":
        passed = not abstained and must_ok and not forbidden
    elif expect == "abstain":
        passed = abstained and not forbidden
    else:  # abstain_or_correct
        passed = (abstained or must_ok) and not forbidden

    gold = question["gold_docs"]
    record: dict[str, Any] = {
        "id": question["id"],
        "category": question["category"],
        "tenant": question["tenant"],
        "user": question["user"],
        "question": question["question"],
        "expect": expect,
        "passed": passed,
        "abstained": abstained,
        "abstain_reason": meta.get("abstain_reason"),
        "must_include_ok": must_ok,
        "forbidden_found": forbidden,
        "answer": answer["content"],
        "citations": [{k: v for k, v in c.items() if k != "content"} for c in citations],
        "retrieved": [{"file": r["file"], "similarity": r["similarity"]} for r in retrieved],
        "latency_ms": {
            "chat_client": round(chat_ms),
            "search_client": round(search_ms),
            "server_total": meta.get("total_ms"),
            **(meta.get("timings_ms") or {}),
        },
        "tokens": meta.get("tokens"),
        "model": meta.get("model"),
        "dropped_citations": meta.get("dropped_citations") or [],
        "quarantined_paragraphs": meta.get("quarantined_paragraphs", 0),
    }
    if gold:
        top_files = {r["file"] for r in retrieved}
        record["recall_at_5"] = sum(g in top_files for g in gold) / len(gold)
    if question["evidence"]:
        found = [
            any(r["file"] == e["doc"] and norm(e["text"]) in norm(r["content"]) for r in retrieved)
            for e in question["evidence"]
        ]
        record["evidence_at_5"] = sum(found) / len(found)
        record["evidence_cited"] = any(
            c["file"] == e["doc"] and norm(e["text"]) in norm(c["content"])
            for e in question["evidence"]
            for c in citations
        )
    if gold and not abstained:
        record["citations_on_gold"] = sum(c["file"] in gold for c in citations)
    record["cross_tenant_citations"] = sum(c["tenant"] != question["tenant"] for c in citations)
    record["restricted_citations"] = sum(
        question["user"] == "member" and c["access_level"] == "admins_only" for c in citations
    )
    record["unknown_citations"] = sum(c["file"] == "?" for c in citations)
    return record


def rate(num: float, den: float) -> float | None:
    return round(num / den, 4) if den else None


def latency(values: list[float]) -> dict[str, Any]:
    if not values:
        return {"n": 0}
    return {
        "n": len(values),
        "p50": round(percentile(values, 50) or 0),
        "p95": round(percentile(values, 95) or 0),
        "mean": round(statistics.fmean(values)),
        "max": round(max(values)),
    }


def summarise(records: list[dict[str, Any]]) -> dict[str, Any]:
    retrieval = [r for r in records if "recall_at_5" in r]
    evidence = [r for r in records if "evidence_at_5" in r]
    should_answer = [r for r in records if r["expect"] == "answer"]
    should_abstain = [r for r in records if r["expect"] == "abstain"]
    flexible = [r for r in records if r["expect"] == "abstain_or_correct"]
    answered_gold = [r for r in records if "citations_on_gold" in r]
    n_citations = sum(len(r["citations"]) for r in answered_gold)
    evidence_answered = [r for r in evidence if not r["abstained"] and r["expect"] == "answer"]
    by_category: dict[str, dict[str, Any]] = defaultdict(lambda: {"passed": 0, "total": 0})
    for r in records:
        by_category[r["category"]]["total"] += 1
        by_category[r["category"]]["passed"] += int(r["passed"])
    for v in by_category.values():
        v["rate"] = rate(v["passed"], v["total"])
    injection = [r for r in records if r["category"] == "injection"]
    llm_ms = [r["latency_ms"]["llm"] for r in records if r["latency_ms"].get("llm") is not None]
    return {
        "questions": len(records),
        "passed": sum(r["passed"] for r in records),
        "pass_rate": rate(sum(r["passed"] for r in records), len(records)),
        "retrieval": {
            "questions": len(retrieval),
            "recall_at_5": rate(sum(r["recall_at_5"] for r in retrieval), len(retrieval)),
            "evidence_at_5": rate(sum(r["evidence_at_5"] for r in evidence), len(evidence)),
        },
        "answers": {
            "expected_answers": len(should_answer),
            "correct": sum(r["passed"] for r in should_answer),
            "correctness": rate(sum(r["passed"] for r in should_answer), len(should_answer)),
        },
        "citations": {
            "answers_with_gold_docs": len(answered_gold),
            "citations": n_citations,
            "citation_accuracy": rate(
                sum(r["citations_on_gold"] for r in answered_gold), n_citations
            ),
            "answers_citing_evidence": rate(
                sum(r["evidence_cited"] for r in evidence_answered), len(evidence_answered)
            ),
            "invented_citations_dropped": sum(len(r["dropped_citations"]) for r in records),
        },
        "abstention": {
            "should_abstain": len(should_abstain),
            "correct_abstentions": sum(r["abstained"] for r in should_abstain),
            "should_answer": len(should_answer),
            "false_abstentions": sum(r["abstained"] for r in should_answer),
            "accuracy": rate(
                sum(r["abstained"] for r in should_abstain)
                + sum(not r["abstained"] for r in should_answer),
                len(should_abstain) + len(should_answer),
            ),
            "misleading_or_flexible_passed": (
                f"{sum(r['passed'] for r in flexible)}/{len(flexible)}"
            ),
        },
        "security": {
            "injection_passed": f"{sum(r['passed'] for r in injection)}/{len(injection)}",
            "forbidden_strings_in_answers": sum(bool(r["forbidden_found"]) for r in records),
            "cross_tenant_citations": sum(r["cross_tenant_citations"] for r in records),
            "member_citations_of_admin_only_docs": sum(r["restricted_citations"] for r in records),
            "citations_to_unknown_documents": sum(r["unknown_citations"] for r in records),
        },
        "latency_ms": {
            "chat_end_to_end": latency([r["latency_ms"]["chat_client"] for r in records]),
            "chat_answered_by_llm": latency(
                [
                    r["latency_ms"]["chat_client"]
                    for r in records
                    if r["latency_ms"].get("llm") is not None
                ]
            ),
            "chat_abstained_before_llm": latency(
                [
                    r["latency_ms"]["chat_client"]
                    for r in records
                    if r["latency_ms"].get("llm") is None
                ]
            ),
            "llm_generation": latency(llm_ms),
            "search_end_to_end": latency([r["latency_ms"]["search_client"] for r in records]),
            "query_embedding": latency(
                [r["latency_ms"]["embed"] for r in records if "embed" in r["latency_ms"]]
            ),
            "hybrid_search_sql": latency(
                [r["latency_ms"]["search"] for r in records if "search" in r["latency_ms"]]
            ),
        },
        "by_category": dict(sorted(by_category.items())),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8080")
    parser.add_argument("--password-file", required=True)
    parser.add_argument("--split", default="test", choices=["test", "dev", "all"])
    parser.add_argument("--only", help="comma-separated question ids")
    parser.add_argument("--out", default="evals/out/results.json")
    parser.add_argument("--label", default="", help="free text stored with the run (config notes)")
    args = parser.parse_args()

    password = Path(args.password_file).read_text(encoding="utf-8").strip()
    questions = load_questions(args.split)
    if args.only:
        wanted = set(args.only.split(","))
        questions = [q for q in questions if q["id"] in wanted]
    clients: dict[tuple[str, str], Client] = {}
    for tenant in MANIFEST["tenants"]:
        for u in tenant["users"]:
            key = (tenant["slug"], "admin" if u["role"] == "tenant_admin" else "member")
            email, slug = account(*key)
            clients[key] = Client(args.base_url, email, password, slug)
    docs = scope_documents(clients)
    not_ready = [i["file"] for d in docs.values() for i in d.values() if i["status"] != "ready"]
    if not_ready:
        raise SystemExit(f"documents not ready: {not_ready}")

    started_at = datetime.now(UTC)
    records: list[dict[str, Any]] = []
    cache: dict[str, str] = {}
    for i, q in enumerate(questions, 1):
        record = evaluate(q, clients[(q["tenant"], q["user"])], docs, cache)
        records.append(record)
        print(
            f"[{i:3}/{len(questions)}] {q['id']:<5} {'PASS' if record['passed'] else 'FAIL'} "
            f"{'abstain' if record['abstained'] else 'answer ':<7} "
            f"{record['latency_ms']['chat_client'] / 1000:6.1f}s  {q['question'][:70]}",
            flush=True,
        )

    models = sorted({r["model"] for r in records if r["model"]})
    result = {
        "run": {
            "started_at": started_at.isoformat(timespec="seconds"),
            "finished_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "split": args.split,
            "label": args.label,
            "llm_models": models,
            "client_python": platform.python_version(),
        },
        "metrics": summarise(records),
        "questions": records,
    }
    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(json.dumps(result["metrics"], indent=2))


if __name__ == "__main__":
    main()
