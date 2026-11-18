# Deployment

Single host with Docker Compose. Tested with Docker 27.3 and Compose 2.29 on Debian 12.

## Requirements

- Docker Engine with the Compose plugin, `openssl`, `python3` (for the evaluation scripts only).
- An Ollama server with `nomic-embed-text` and `llama3.1:8b` (or another chat model), on the same
  host or reachable over the network. CPU-only works; expect roughly 50 s per answer with an 8B
  model on 8 server cores (see the benchmark report). A GPU makes answers several times faster.
- About 2 GiB of RAM for the application stack, plus about 6 GiB for an 8B model.

## First start

```bash
git clone https://github.com/ali-fakhrabadi/rag-support-chatbot.git
```

```bash
cd rag-support-chatbot && scripts/init-secrets.sh && cp .env.example .env
```

Edit `.env`:

- `RAG_OLLAMA_BASE_URL`: where Ollama runs. Ollama on the Docker host:
  `http://host.docker.internal:11434` (Ollama must listen on an address the containers can reach,
  for example `OLLAMA_HOST=0.0.0.0` behind a firewall). Optional bundled Ollama: see below.
- `RAG_OLLAMA_EMBEDDING_BASE_URL` (optional): a different Ollama for embeddings only.
- `RAG_LLM_MODEL`: the exact model tag you pulled, for example `llama3.1:8b`.

Pull the models on the Ollama host (nothing is downloaded automatically):

```bash
ollama pull nomic-embed-text && ollama pull llama3.1:8b
```

Start everything:

```bash
docker compose up -d --build
```

`migrate` runs Alembic as the database owner and exits; `api`, `worker` and `web` start after it.
Check readiness (database with pgvector, and Redis):

```bash
curl -s http://127.0.0.1:8080/api/ready
```

Create the first workspace and its admin (the password is read from the prompt, never from the
command line):

```bash
docker compose exec api python -m app.cli create-workspace --slug acme --name "Acme" --admin-email admin@acme.example --full-name "Acme Admin"
```

Open `http://127.0.0.1:8080`, sign in, upload documents on the Documents page and ask questions.
Admins add members on the API (`POST /api/v1/tenants/current/members`) or with
`python -m app.cli add-user`.

## Optional bundled Ollama

```bash
docker compose --profile ollama up -d ollama
```

```bash
docker compose exec ollama ollama pull nomic-embed-text && docker compose exec ollama ollama pull llama3.1:8b
```

Then set `RAG_OLLAMA_BASE_URL=http://ollama:11434` and `docker compose up -d`.

## Optional OpenAI

Put the key in `secrets/openai_api_key`, set `RAG_LLM_PROVIDER=openai` and `RAG_LLM_MODEL` (for
example `gpt-4o-mini`). Retrieved passages and questions are then sent to OpenAI; keep the
embeddings local unless you also accept that for document text. Switching the embedding provider
requires a re-index ([rag-pipeline.md](rag-pipeline.md#re-indexing)).

## Exposure and TLS

`web` is published on `127.0.0.1:8080` by default (`WEB_BIND`, `WEB_PORT`). For remote users put
a TLS-terminating reverse proxy in front (Caddy, Traefik, nginx or a cloud load balancer) and
keep `RAG_TRUST_PROXY_HEADERS=true` only if that proxy sets `X-Real-IP`. PostgreSQL and Redis
are never published.

## Configuration reference

All settings are environment variables with the `RAG_` prefix (see `.env.example` and
`backend/app/core/config.py`). The most relevant:

| Variable | Default | Meaning |
| --- | --- | --- |
| `RAG_ENVIRONMENT` | `production` | `production` requires a JWT secret of 32+ characters |
| `RAG_ENABLE_DOCS` | `true` | Serve OpenAPI docs at `/api/docs` |
| `RAG_OLLAMA_BASE_URL` | `http://ollama:11434` | Ollama for generation (and embeddings unless overridden) |
| `RAG_OLLAMA_EMBEDDING_BASE_URL` | unset | Separate Ollama for embeddings |
| `RAG_EMBEDDING_MODEL` / `RAG_EMBEDDING_DIM` | `nomic-embed-text` / `768` | Must match the vector column |
| `RAG_LLM_PROVIDER` / `RAG_LLM_MODEL` | `ollama` / `llama3.1:8b` | `openai` needs `secrets/openai_api_key` |
| `RAG_LLM_TIMEOUT_S` | `300` | nginx allows 330 s for `/api` |
| `RAG_LLM_NUM_THREAD` | unset | Ollama CPU threads |
| `RAG_RETRIEVAL_TOP_K` | `5` | Passages sent to the model |
| `RAG_MIN_SIMILARITY` | `0.54` | Similarity gate, calibrated for `nomic-embed-text` |
| `RAG_CHUNK_SIZE` / `RAG_CHUNK_OVERLAP` | `1200` / `200` | Characters |
| `RAG_MAX_UPLOAD_MB` / `RAG_MAX_PDF_PAGES` | `20` / `300` | Upload limits |
| `RAG_RATE_LIMIT_LOGIN_PER_MINUTE` | `10` | Per IP and per email |
| `RAG_RATE_LIMIT_CHAT_PER_MINUTE` | `20` | Per user (search: 3x this) |
| `RAG_RATE_LIMIT_UPLOAD_PER_MINUTE` | `30` | Per user |
| `RAG_TRUST_PROXY_HEADERS` | `false` | Read the client IP from `X-Real-IP` |
| `RAG_CORS_ORIGINS` | empty | Only needed if the SPA is served from another origin |

## Backups

Everything is in PostgreSQL (documents' text, chunks, vectors, users, conversations, audit).

```bash
docker compose exec -T postgres pg_dump -U rag_owner -Fc rag > rag-$(date +%F).dump
```

Restore into a fresh stack with `pg_restore`. Redis only holds the queue and rate-limit counters.

## Upgrades

Pull the new version, then `docker compose up -d --build`; `migrate` applies new Alembic
migrations before the API starts. Read `CHANGELOG.md` for changes that need a re-index.

## Hardening already in place

Non-root users, read-only root filesystems with tmpfs `/tmp`, `cap_drop: ALL`,
`no-new-privileges`, internal network for data services, Docker secrets, digest-pinned images,
and fail-closed rate limits. See [security.md](security.md).
