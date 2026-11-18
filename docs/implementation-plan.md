# Implementation plan

Secure RAG Knowledge Assistant: a self-hostable service that answers questions from a team's
own documents, cites its sources, keeps every tenant's data apart and abstains when the
evidence is not there.

## Environment (inspected 2026-11-18)

| Machine | Role | Resources |
|---|---|---|
| Lab server A | Docker host for the whole application stack, tests, evaluation, k6 | Xeon Gold 6138, 10 vCPU, 9.7 GiB RAM (about 7 GiB free), Debian 12, Docker 27.3, Compose 2.29 |
| Lab server B | Model host: Ollama 0.12.9 (CPU only) | 12 vCPU (Ollama limited to 8), 15 GiB RAM (Ollama capped at 10 GiB), Debian 12 |
| Developer workstation | Editing, git, GitHub CLI, browser | No workloads run here |

No GPU is available. Every measurement in this repository comes from these CPU-only machines.

## Technology choices

Versions were checked against PyPI, npm and Docker Hub on 2026-11-18. Python dependencies are
pinned to releases at least one week old, with hashes.

| Area | Choice | Why |
|---|---|---|
| Runtime | Python 3.12 (`python:3.12-slim-trixie`) | Supported by every dependency; image already used by the author's other projects |
| API | FastAPI + Pydantic v2 | Typed request/response models and OpenAPI out of the box |
| Database | PostgreSQL 17 + pgvector 0.8 (`pgvector/pgvector:0.8.x-pg17`) | Vectors, full-text search and relational data in one transactional store; iterative HNSW scans keep filtered vector search correct |
| ORM / migrations | SQLAlchemy 2 (sync, psycopg 3) + Alembic | One code path for the API and the Celery worker |
| Jobs | Celery 5 with Redis 7.4 | Retries, time limits and acknowledgement after completion |
| Embeddings | Ollama `nomic-embed-text` (768 dims, Apache-2.0) | Small, CPU-friendly, strong retrieval quality; uses `search_query:` / `search_document:` prefixes |
| LLM | Ollama `llama3.1:8b` (Q4_K_M) by default, OpenAI optional | Runs locally on CPU; structured JSON output through Ollama's `format` schema |
| Auth | PyJWT (HS256 access tokens) + rotating refresh tokens stored hashed, Argon2id passwords | Short-lived access tokens; refresh reuse is detected |
| Extraction | pypdf (PDF), UTF-8 text (TXT, Markdown) | Pure Python, no active content executed; DOCX left out (see limitations) |
| Frontend | React + TypeScript + Vite, served by nginx with `/api` proxied | Same origin, so no permissive CORS |
| Tests | pytest (unit, integration against real PostgreSQL/Redis, real-model end-to-end), Vitest, Playwright, k6 | Mocks only for isolated branches |

## Architecture in one paragraph

A modular monolith. `api/` holds HTTP routes only. `core/` holds settings, security
(passwords, tokens, rate limits) and the request dependencies that resolve the caller's tenant
and role from the database on every request. `services/` holds the use cases (documents,
ingestion, retrieval, answering, conversations, audit). `ingestion/` validates, extracts,
normalises and chunks files. `providers/` holds the embedding and LLM clients behind small
interfaces. `rag/` holds rank fusion, prompt building and citation validation. The Celery worker
reuses the same services.

## Tenant isolation (defence in depth)

1. Every tenant-owned table carries `tenant_id`; every query in the services filters on the
   caller's tenant, which comes from the membership row, not from the client.
2. PostgreSQL Row-Level Security is forced on the tenant-owned tables. The session sets
   `app.tenant_id` with `SET LOCAL` per transaction; an unset value matches no rows.
3. Retrieval applies tenant, document-access and document-status filters inside the SQL that
   ranks chunks, so unauthorised text never reaches the prompt.
4. Citation validation rejects any source id the model returns that was not in the authorised
   context.

## Order of work

1. Foundation: settings, database models, migrations, RLS, auth, RBAC, audit, health.
2. Ingestion: upload validation, job queue, extraction, chunking, real embeddings.
3. Retrieval and answering: vector + full-text + RRF, thresholds, grounded JSON answers,
   citation validation, abstention, prompt-injection defences, conversations.
4. Frontend: login, documents, ingestion status, chat, citations, passage viewer.
5. Tests: unit, integration (real PostgreSQL/pgvector, Redis, Celery), API/security, frontend,
   real-model end-to-end with two tenants.
6. Evaluation: synthetic document set, 100+ labelled questions, deterministic metrics,
   latency, k6 load test.
7. Docker Compose hardening, CI (lint, types, tests, coverage, pip-audit, gitleaks, Trivy,
   frontend), documentation, branding and real screenshots.
8. Publishing: GitHub repository, Upwork Portfolio item 07, Upwork Project Catalog service.

## Reporting rules

Results are reported in four groups (unit, integration, end-to-end, evaluation). Anything that
could not run is marked `BLOCKED` or `NOT RUN` with the reason; nothing is estimated.
