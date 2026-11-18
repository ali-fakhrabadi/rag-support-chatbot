"""Render the benchmark tables (Markdown) from one or more results files of run_eval.py.

    python evals/report.py docs/evaluation/results-run1-baseline.json \
        docs/evaluation/results-run2.json > /tmp/tables.md

The narrative of docs/evaluation/benchmark-report.md is written by hand; every number in its
tables comes from this script.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

CATEGORIES = [
    "answerable",
    "numeric",
    "multi_doc",
    "similar_source",
    "unanswerable",
    "misleading",
    "injection",
    "permission",
    "isolation",
]


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value * 100:.1f}%"


def secs(ms: float | None) -> str:
    return "n/a" if ms is None else f"{ms / 1000:.1f} s"


def failure_kind(q: dict[str, Any]) -> str:
    if q["forbidden_found"]:
        return "forbidden text in answer"
    if q["expect"] == "answer" and q["abstained"]:
        return "abstained, answer expected"
    if q["expect"] == "abstain" and not q["abstained"]:
        return "answered, abstention expected"
    return "required fact missing"


def summary(runs: list[tuple[str, dict[str, Any]]]) -> str:
    header = "| Metric | " + " | ".join(name for name, _ in runs) + " |"
    lines = [header, "|---|" + "---|" * len(runs)]

    def row(label: str, fn: Any) -> None:
        lines.append(f"| {label} | " + " | ".join(fn(r["metrics"]) for _, r in runs) + " |")

    row(
        "Questions passed (all categories)",
        lambda m: f"{m['passed']}/{m['questions']} ({pct(m['pass_rate'])})",
    )
    row("Recall@5 (gold documents in top 5)", lambda m: pct(m["retrieval"]["recall_at_5"]))
    row("Evidence@5 (gold passage in top 5)", lambda m: pct(m["retrieval"]["evidence_at_5"]))
    row(
        "Answer correctness",
        lambda m: (
            f"{m['answers']['correct']}/{m['answers']['expected_answers']}"
            f" ({pct(m['answers']['correctness'])})"
        ),
    )
    row(
        "Citation accuracy (citations on a gold document)",
        lambda m: f"{pct(m['citations']['citation_accuracy'])} of {m['citations']['citations']}",
    )
    row(
        "Answers whose citations contain the evidence",
        lambda m: pct(m["citations"]["answers_citing_evidence"]),
    )
    row(
        "Correct abstentions",
        lambda m: f"{m['abstention']['correct_abstentions']}/{m['abstention']['should_abstain']}",
    )
    row(
        "False abstentions (answer expected)",
        lambda m: f"{m['abstention']['false_abstentions']}/{m['abstention']['should_answer']}",
    )
    row("Abstention accuracy", lambda m: pct(m["abstention"]["accuracy"]))
    row(
        "Abstain-or-correct questions (false premises, one injection)",
        lambda m: m["abstention"]["misleading_or_flexible_passed"],
    )
    row("Prompt-injection tests passed", lambda m: m["security"]["injection_passed"])
    row(
        "Answers containing forbidden text",
        lambda m: str(m["security"]["forbidden_strings_in_answers"]),
    )
    row("Cross-workspace citations", lambda m: str(m["security"]["cross_tenant_citations"]))
    row(
        "Admin-only citations shown to members",
        lambda m: str(m["security"]["member_citations_of_admin_only_docs"]),
    )
    row(
        "Invented citations removed by validation",
        lambda m: str(m["citations"]["invented_citations_dropped"]),
    )
    return "\n".join(lines)


def categories(runs: list[tuple[str, dict[str, Any]]]) -> str:
    lines = [
        "| Category | Questions | " + " | ".join(name for name, _ in runs) + " |",
        "|---|---|" + "---|" * len(runs),
    ]
    for cat in CATEGORIES:
        first = runs[0][1]["metrics"]["by_category"].get(cat)
        if not first:
            continue
        cells = []
        for _, r in runs:
            c = r["metrics"]["by_category"][cat]
            cells.append(f"{c['passed']} ({pct(c['rate'])})")
        lines.append(f"| {cat} | {first['total']} | " + " | ".join(cells) + " |")
    return "\n".join(lines)


def latency(name: str, result: dict[str, Any]) -> str:
    lat = result["metrics"]["latency_ms"]
    labels = {
        "chat_end_to_end": "Question to answer (client, through nginx)",
        "chat_answered_by_llm": "  of which: answers that called the model",
        "chat_abstained_before_llm": "  of which: abstained before the model",
        "llm_generation": "Model generation (server side)",
        "search_end_to_end": "Search request (client)",
        "query_embedding": "Query embedding (server side)",
        "hybrid_search_sql": "Hybrid SQL search (server side)",
    }
    lines = [
        f"**{name}**",
        "",
        "| Step | n | p50 | p95 | mean | max |",
        "|---|---|---|---|---|---|",
    ]
    for key, label in labels.items():
        s = lat.get(key, {"n": 0})
        if not s.get("n"):
            lines.append(f"| {label} | 0 | | | | |")
            continue
        fmt = (
            secs
            if s["p50"] >= 1000 or key.startswith("chat") or key == "llm_generation"
            else (lambda v: f"{v:.0f} ms")
        )
        cells = " | ".join(fmt(s[k]) for k in ("p50", "p95", "mean", "max"))
        lines.append(f"| {label} | {s['n']} | {cells} |")
    tokens = [q["tokens"] for q in result["questions"] if q.get("tokens")]
    if tokens:
        prompt = sorted(t["prompt"] for t in tokens)
        completion = sorted(t["completion"] for t in tokens)
        lines += [
            "",
            f"Prompt tokens per answer: median {prompt[len(prompt) // 2]}, max {prompt[-1]}; "
            f"completion tokens: median {completion[len(completion) // 2]}, max {completion[-1]}.",
        ]
    return "\n".join(lines)


def failures(name: str, result: dict[str, Any], questions: dict[str, Any]) -> str:
    lines = [
        f"**{name}**: {sum(not q['passed'] for q in result['questions'])} failures",
        "",
        "| Id | Category | Question | Required | Answer (first 160 chars) | Why it failed |",
        "|---|---|---|---|---|---|",
    ]
    for q in result["questions"]:
        if q["passed"]:
            continue
        required = (
            " AND ".join("(" + " / ".join(g) + ")" for g in questions[q["id"]]["must_include"])
            or "-"
        )
        answer = q["answer"].replace("\n", " ").replace("|", "\\|")[:160]
        cells = [q["id"], q["category"], q["question"], required, answer, failure_kind(q)]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines)


def main() -> None:
    paths = [Path(p) for p in sys.argv[1:]]
    runs = [(f"Run {i}", json.loads(p.read_text(encoding="utf-8"))) for i, p in enumerate(paths, 1)]
    qfile = Path(__file__).parent / "questions.jsonl"
    questions = {
        json.loads(line)["id"]: json.loads(line)
        for line in qfile.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }
    out = [
        "## Summary",
        "",
        summary(runs),
        "",
        "## By category",
        "",
        categories(runs),
        "",
        "## Latency",
        "",
    ]
    for name, r in runs:
        out += [latency(name, r), ""]
    out += ["## Failures", ""]
    for name, r in runs:
        out += [failures(name, r, questions), ""]
    print("\n".join(out))


if __name__ == "__main__":
    main()
