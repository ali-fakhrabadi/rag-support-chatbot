# Progress log

## 2026-11-18

- Inspected the environment. The application stack runs on lab server A (Docker 27.3,
  Compose 2.29, 10 vCPU, 9.7 GiB RAM); models run on lab server B (Ollama 0.12.9, CPU only).
  The developer workstation only edits code and talks to GitHub.
- Docker Hub, PyPI, npm and the Ollama registry are reachable from both servers.
- Pulled `nomic-embed-text` (digest `0a109f422b47`) on the model host; a test call returned
  two 768-dimension vectors. `llama3.1:8b` (digest `46e0c10c039e`) was already present.
- GitHub: authenticated as `ali-fakhrabadi`; `rag-support-chatbot` does not exist yet.
- Wrote `docs/implementation-plan.md`.

## 2026-11-18 — web app

- React 19 + TypeScript + Vite single-page app: sign-in (with optional workspace), document
  list with live ingestion status, admin upload with access level, delete, document details
  (job progress, attempts, embedding model, extracted passages), chat with conversation
  history, inline citation buttons, a passage drawer that loads the cited chunk with the
  reader's own permissions, an explicit "No supported answer" state, and loading/empty/error
  states.
- Served by an unprivileged nginx (read-only root filesystem) that proxies `/api` and sets a
  strict CSP; the image is built from a hash-checked `package-lock.json`.
- Measured: 21 Vitest tests pass; `tsc --noEmit` clean; statement coverage 85.9 %, line
  coverage 89.2 %. Production bundle 280.6 kB JS (88.2 kB gzip), 10.3 kB CSS.

## 2026-11-18 — evaluation, hardening, CI

- Evaluation corpus (15 fictional documents, two workspaces) and 159 labeled questions; seeding
  through the public API; dev-split calibration of the similarity gate (0.54, recall first,
  because answerable and unanswerable similarity ranges overlap for `nomic-embed-text`).
- Embeddings moved to their own Ollama (`RAG_OLLAMA_EMBEDDING_BASE_URL`) after measuring about
  10 s per passage on the shared model host versus 0.35 s on an idle host; full re-index of the
  corpus in 10 s.
- Run 1 on the test split: 115/135 passed, Recall@5 100 %, 0 cross-workspace and 0 admin-only
  citations, one injection failure (the model repeated an injected payload). Fixes: paragraph
  quarantine before generation, complete-answer rule, echoed fences turned into markers.
- Found and fixed during deployment: stale API image, nginx caching the API container address
  (502 after restarts), `docker compose exec` swallowing a script's stdin, web image with fixed
  Alpine CVEs (Trivy), a fake JWT in a test flagged by gitleaks (documented).
- Browser tests (Playwright) across two workspaces; GitHub Actions green on all jobs
  (backend against real PostgreSQL + pgvector and Redis, frontend, gitleaks, Trivy, Compose
  stack with browser tests).

## 2026-11-19 — run 2, screenshots, load tests, publishing

- Run 2 on the test split (after the quarantine and complete-answer changes): 129/135 passed,
  answer correctness 93/99, 7/7 injection tests, 0 cross-workspace and 0 admin-only citations.
  Remaining failures: one retrieval miss and five incomplete or declined multi-fact answers
  from the 8B model (see the benchmark report).
- All 13 browser tests pass against the deployment, including the 4 that use the real models;
  they produced the screenshots in `assets/screenshots`. The evaluation accounts' stored
  conversations were cleared before the screenshot run so the history shows only the test
  questions.
- Passage display joins hard-wrapped source lines (keeps paragraphs, headings and lists); long
  conversation titles in the history are truncated with an ellipsis.
