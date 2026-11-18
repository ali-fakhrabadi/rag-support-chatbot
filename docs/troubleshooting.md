# Troubleshooting

| Symptom | Likely cause | What to do |
| --- | --- | --- |
| `/api/ready` returns 503 | PostgreSQL or Redis not reachable, or pgvector missing | `docker compose ps`; `docker compose logs postgres redis migrate` |
| `migrate` exits with a permission error on a secret | Secret files not readable by the container user | Re-run `scripts/init-secrets.sh` (it sets `secrets/` to 700 and files to 644 for the container users) |
| API refuses to start: `RAG_JWT_SECRET must be at least 32 characters` | Missing or short secret in production mode | `scripts/init-secrets.sh`, or set `RAG_ENVIRONMENT=development` for local experiments only |
| API refuses to start: `RAG_EMBEDDING_DIM=... but the database column has 768` | Embedding model with another dimension | Use a 768-dimension model or migrate the column; see [rag-pipeline.md](rag-pipeline.md#re-indexing) |
| Chat returns 503 "language model is unavailable" | Ollama unreachable, model tag not pulled, or timeout | `curl $RAG_OLLAMA_BASE_URL/api/tags`; check `RAG_LLM_MODEL` matches a pulled tag exactly (`llama3.1:8b` vs `llama3.1:latest`) |
| Documents stay "Indexing", jobs show `waiting_retry` | Embedding requests time out (slow or busy CPU host) | Check the worker log for `embedding failed: timeout`; give embeddings their own Ollama (`RAG_OLLAMA_EMBEDDING_BASE_URL`), raise `RAG_EMBED_TIMEOUT_S`, or lower `RAG_EMBEDDING_BATCH_SIZE` |
| Document "Failed: No extractable text found" | Scanned PDF (images only) | Run OCR first; OCR is not part of this project |
| Document "Failed: Encrypted PDFs are not supported" | Password-protected PDF | Remove the protection and upload again |
| Upload rejected with 409 | The same file is already in this workspace | Delete the old copy first if you want to re-ingest it |
| Every question abstains | Similarity gate too high for your model or documents, or documents not ready | Check the Documents page; run `evals/calibrate.py`-style calibration on your own questions and set `RAG_MIN_SIMILARITY` |
| Answers are slow (tens of seconds) | CPU inference | Expected on CPU (see the benchmark report). Use a GPU host, a smaller model, or fewer passages (`RAG_RETRIEVAL_TOP_K`) |
| "Too many requests, slow down" | Rate limit (login per IP/email, chat/search/upload per user) | Wait a minute; tune `RAG_RATE_LIMIT_*` |
| Web returns 502 after restarting the API | Old nginx configuration that resolved `api` once | Current `nginx.conf` resolves through Docker DNS per request; rebuild `web` |
| Signed out after a reload in a new tab | Refresh token is per tab (`sessionStorage`) by design | Sign in again; see [security.md](security.md) |

## Useful commands

```bash
docker compose logs -f api worker
```

```bash
docker compose exec postgres psql -U rag_owner -d rag -c "select filename, status, chunk_count from documents"
```

```bash
docker compose exec api python -m app.cli reindex --workspace <slug>
```
