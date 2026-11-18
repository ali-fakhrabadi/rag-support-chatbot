# Evaluation

A small, fully synthetic benchmark for the assistant, run against a real deployment with real
models. Results and methodology are in [docs/evaluation](../docs/evaluation/).

## Corpus

Two fictional organisations, written for this project (no real company data):

| Workspace | Documents | Formats |
| --- | --- | --- |
| `larkspur` (Larkspur Outfitters, outdoor retailer) | 12, one of them admin-only | Markdown, TXT, PDF |
| `fenwick` (Fenwick Dental Group, dental clinic) | 3 | Markdown, TXT |

The corpus deliberately contains:

- near-duplicate sources with different facts (regular vs. holiday shipping, the lifetime
  warranty vs. the paid protection plan, the loyalty tier vs. the protection plan names),
- a document with a prompt-injection payload and a canary string (`vendor-onboarding-notes.txt`),
- an admin-only document (`compensation-bands.md`) to test document permissions,
- overlapping topics across the two workspaces (time off, refunds, opening hours) to test
  tenant isolation.

The PDFs are rendered from `corpus/*/pdf-src/*.md` by `build_corpus.py` (deterministic output).

## Questions

`questions.jsonl` holds 159 labeled questions: 24 in the `dev` split (used only to choose the
similarity threshold) and 135 in the `test` split (used for every reported number).

| Category | Test questions | Expected behaviour |
| --- | --- | --- |
| answerable | 47 | answer with the listed facts and cite the right document |
| numeric | 24 | as above, for numbers, amounts and durations |
| multi_doc | 9 | combine facts from two documents (both must be retrieved) |
| similar_source | 10 | pick the right one of two similar documents |
| unanswerable | 16 | abstain |
| misleading | 9 | abstain or correct the false premise |
| injection | 7 | answer without following injected instructions or leaking the prompt |
| permission | 5 | members never see admin-only content; admins do |
| isolation | 8 | never use or cite the other workspace's documents |

Each question lists its gold documents, evidence sentences (checked against the corpus when the
file is generated), required facts (`must_include`: every group must match, any alternative in a
group counts) and forbidden strings (`must_not_include`). Matching is case-insensitive after
normalising dashes, thousands separators and `a.m./p.m.`. There is no model-as-judge.

## Running

On the Docker host, with the stack running and the models available:

```bash
evals/seed.sh
```

```bash
docker compose run --rm --no-deps -T -v "$PWD/evals:/evals:ro" api python /evals/calibrate.py > evals/out/calibration-dev.json
```

```bash
docker run --rm --network rag-support-chatbot_egress --user "$(id -u):$(id -g)" \
  -v "$PWD/evals:/evals:ro" -v "$PWD/evals/out:/out" -v "$PWD/secrets/eval_password:/run/secrets/eval_password:ro" \
  python:3.12.15-slim-trixie python /evals/run_eval.py --base-url http://web:8080 \
  --password-file /run/secrets/eval_password --split test --out /out/test.json
```

`run_eval.py` uses only the Python standard library, so any Python 3.12 can run it against any
deployment URL.
