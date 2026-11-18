# Limitations

Known limits of this version, stated plainly.

## Product scope

- File types: PDF (with a text layer), TXT and Markdown. No DOCX, HTML, spreadsheets, images or
  OCR for scanned PDFs.
- English full-text search configuration. Embedding retrieval works for other languages as far as
  `nomic-embed-text` does, but keyword search and the evaluation are English only.
- Two roles (Tenant Admin, Member) and two document access levels. No per-user or per-group
  document permissions, no SSO, no MFA.
- No streaming of answers; the UI shows a progress indicator until the full answer arrives.
- Workspaces are created with a CLI command; there is no self-service sign-up or billing.
- Member management is available through the API; the web app does not have a members page yet.

## Answer quality

- Measured on one synthetic corpus of 15 short documents with a CPU-only 8B model; results on
  other documents, languages or models will differ. See the
  [benchmark report](evaluation/benchmark-report.md) for the numbers and failure analysis.
- The similarity gate cannot separate answerable from unanswerable questions for this embedding
  model (the score ranges overlap), so abstention relies on the model and on citation
  validation.
- Multi-part questions and questions that need two documents are the weakest category.
- Scoring in the evaluation is rule based (required and forbidden strings), which can miss a
  correct paraphrase or accept a lucky one; the report lists every failure.

## Performance

- On the CPU-only lab machines used here an answer takes about 50 s at the median, and the model
  host serves one generation at a time. This suits a small team or a pilot, not many concurrent
  users. A GPU, a smaller model or a hosted model changes this.
- Ingestion speed depends on the embedding host: the 15-document evaluation corpus (60 chunks)
  was ingested in 22 s with embeddings on an idle 10-vCPU VM, but the same work took about 17
  minutes, with timeouts and retries, against a busy shared host during development.

## Security

- Prompt-injection quarantine is pattern based and can be bypassed by rephrased instructions;
  admins who upload documents are trusted within their workspace.
- No antivirus scanning of uploads.
- Tenants share one database (isolation by Row-Level Security and application filters).
- No independent security audit or penetration test has been performed.

## Operations

- Single-host Docker Compose; no Kubernetes manifests or high-availability setup.
- No built-in metrics endpoint; logs are structured JSON for an external collector.
