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
