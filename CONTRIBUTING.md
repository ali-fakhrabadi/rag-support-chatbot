# Contributing

Issues and pull requests are welcome. Please keep changes focused and include tests.

## Development setup

The backend tests need PostgreSQL with pgvector and Redis. The simplest way is the Compose
stack plus the development image:

```bash
scripts/init-secrets.sh && cp .env.example .env && docker compose up -d postgres redis
```

```bash
docker build --target dev -t rag-support-chatbot-backend:devtools backend
```

```bash
docker run --rm --network rag-support-chatbot_data -v "$PWD/backend:/app" -w /app -u "$(id -u)" -e HOME=/tmp -e RAG_DB_HOST=postgres -e RAG_DB_ADMIN_USER=rag_owner -e RAG_DB_ADMIN_PASSWORD="$(cat secrets/db_owner_password)" -e RAG_DB_PASSWORD="$(cat secrets/db_app_password)" -e RAG_REDIS_URL=redis://redis:6379/1 rag-support-chatbot-backend:devtools pytest
```

The integration tests create and drop their own database (`rag_test`) and use Redis database 1,
so they do not touch a running deployment's data.

Frontend:

```bash
cd frontend && npm ci && npm test && npm run build
```

## Checks that CI runs

- `ruff check .` and `ruff format --check .` (Python, from the repository root)
- `mypy app` (in `backend/`)
- `pytest --cov=app --cov-fail-under=90` (in `backend/`)
- `pip-audit`, `npm audit --omit=dev`, gitleaks, Trivy
- `npm run typecheck`, `npm run test:coverage`, `npm run build` (in `frontend/`)
- browser tests against the Compose stack (`e2e/run.sh -m "not models"`)

## Dependencies

Python dependencies are locked with hashes. Edit `backend/requirements*.in`, then regenerate the
locks with `scripts/lock.sh` (see the comment at its top). Use releases that are at least a week
old.

## Evaluation

Changes to retrieval, prompts or models should be measured with the evaluation in `evals/`
(see its README) and the numbers added to the benchmark report, including regressions.
