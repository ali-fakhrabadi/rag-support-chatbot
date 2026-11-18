# Evaluation methodology

The goal is to measure, on real models and a real deployment, whether the assistant finds the
right passages, answers correctly with valid citations, declines when the documents do not
answer, resists injected instructions and never crosses workspace or permission boundaries.
Everything here is reproducible with the scripts in [`evals/`](../../evals/).

## What is measured

| Metric | Definition | Over |
| --- | --- | --- |
| Recall@5 | Share of a question's gold documents that appear among the 5 passages returned by `POST /api/v1/search` (the same retriever, filters and similarity gate the chat uses) | Questions with gold documents |
| Evidence@5 | Share of a question's gold evidence sentences that are contained in a top-5 passage from the right document (stricter than Recall@5: the right *passage*, not just the right file) | Questions with evidence |
| Answer correctness | The answer is not an abstention, contains every required fact (each group of `must_include` must match one alternative) and no forbidden string | Questions that expect an answer |
| Citation accuracy | Share of all citations in non-abstained answers that point to a gold document | Answers to questions with gold documents |
| Answers citing evidence | Share of correct-category answers whose cited passages include the gold evidence sentence | Answered questions with evidence |
| Abstention accuracy | Abstained when expected plus answered when expected, over both groups | Questions that expect an answer or an abstention |
| Injection | Pass rate of the injection category; count of answers containing a forbidden string (canary, attacker address, prompt text) | All questions |
| Isolation and permissions | Citations to another workspace's documents; citations of admin-only documents in a member's answer | All questions |
| Latency | Client-side end-to-end time of `POST /api/v1/chat` and `POST /api/v1/search`, plus server-side timings stored with each answer (embedding, SQL search, generation), as p50, p95 (nearest rank), mean and max | All questions |

Matching is case-insensitive after normalising dashes, thousands separators and `a.m./p.m.`.
There is no model-as-judge: rules are transparent but strict, so a correct answer phrased
differently (for example a bare "2" where the rule expects "2 days") counts as a failure. The
benchmark report lists every failure with the actual answer so readers can judge.

## Data

- **Corpus:** 15 fictional documents written for this project in two workspaces, 12 for an
  outdoor retailer (one admin-only) and 3 for a dental clinic; Markdown, TXT and PDF (PDFs
  rendered deterministically from Markdown sources). It contains near-duplicate sources with
  different facts, an injected instruction with a canary, and overlapping topics across the two
  workspaces. No real company data. See [`evals/README.md`](../../evals/README.md).
- **Questions:** 159 labeled questions in `evals/questions.jsonl`: a **dev split of 24** used
  only to choose the similarity gate, and a **test split of 135** used for every reported
  number: answerable 47, numeric 24, multi-document 9, similar sources 10, unanswerable 16,
  misleading premise 9, prompt injection 7, document permission 5, tenant isolation 8. Evidence
  sentences are checked against the corpus when the file is generated.
- **Accounts:** each question is asked as a specific user (member or admin) of a specific
  workspace, through the public API behind nginx, exactly like a browser.

## Procedure

1. Start the Compose stack and seed the two workspaces through the API (`evals/seed.sh`).
2. Calibrate the gate on the dev split only (`evals/calibrate.py`), set
   `RAG_MIN_SIMILARITY` and restart the API.
3. Run `evals/run_eval.py --split test`. Every question is asked in a fresh conversation; for
   each one the script also runs a search and fetches every cited passage.
4. Save the raw output (`results-run*.json`): run label, configuration, per-question answers,
   citations, retrieved files and similarities, latencies and token counts.

## Runs and honesty rules

- The first run on the test split is the **baseline**. Its numbers are reported as measured.
- If the failure analysis of a run leads to a change in the system, the change is described,
  the full test split is run again, and **both** runs are reported. A run made after looking at
  test failures is no longer a clean held-out measurement; the report says so.
- Scoring rules are never edited after a run to turn failures into passes.
- Nothing is estimated: anything not run is marked NOT RUN.

## Limits of this evaluation

- One small synthetic corpus with short, clean documents; real document sets are larger, messier
  and contain tables and scans.
- 135 test questions give wide uncertainty on small categories (5 to 10 questions each).
- One model configuration on CPU-only hardware; latency depends heavily on hardware.
- Rule-based scoring (see above).
