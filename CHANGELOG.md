# Changelog

All notable changes to this project. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses
[Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-11-19

First public version.

### Added

- FastAPI service with versioned REST API (`/api/v1`) and OpenAPI docs: authentication with
  Argon2id passwords, 15-minute access tokens and rotating refresh tokens with reuse detection;
  workspaces with Tenant Admin and Member roles; member management; audit log.
- PostgreSQL 17 + pgvector data model with forced Row-Level Security on every tenant-owned table
  and a non-owner application role; Alembic migrations.
- Ingestion of PDF, TXT and Markdown through a Celery worker: validation, page- and
  heading-aware chunking, batched embeddings, progress, retries, failure messages, safe deletion
  and duplicate detection.
- Hybrid retrieval (HNSW vector search + full-text search, reciprocal rank fusion) with
  permission, status and model filters inside SQL, and a calibrated similarity gate.
- Grounded answers with Ollama (default) or OpenAI: JSON-schema output, citation validation,
  abstention, conversation history, prompt-injection defences including paragraph quarantine.
- React + TypeScript web app: sign-in, documents with live ingestion status, upload with access
  level, document details with extracted passages, chat with history, inline citations and a
  source-passage drawer, explicit "No supported answer" state.
- Docker Compose deployment with non-root, read-only, capability-less containers, an internal
  data network and Docker secrets; optional Ollama profile.
- Tests: backend unit and integration tests against real PostgreSQL/pgvector, Redis and Celery;
  frontend tests; browser end-to-end tests across two workspaces, including real-model tests.
- Evaluation: synthetic two-workspace corpus, 159 labeled questions (135 test), deterministic
  scoring, dev-split calibration, two documented runs; k6 load tests.
- CI: lint, types, tests with coverage gate, pip-audit, npm audit, gitleaks, Trivy image scans
  and browser tests against the Compose stack.
