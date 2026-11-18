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
