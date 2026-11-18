# API examples

The API is versioned under `/api/v1`; interactive docs are at `/api/docs` (OpenAPI 3.1). Errors
always have the same shape: `{"error": {"code", "message", "request_id", "details?"}}`. Every
response carries an `X-Request-ID` header that also appears in the logs.

The responses below were captured from the evaluation deployment (fictional data) and shortened
where marked.

```bash
BASE=http://127.0.0.1:8080
```

## Sign in

```bash
curl -s $BASE/api/v1/auth/login -H 'Content-Type: application/json' -d '{"email":"agent@larkspur.example","password":"<password>","workspace":"larkspur"}'
```

```json
{"access_token": "<jwt>", "refresh_token": "<opaque>", "token_type": "bearer", "expires_in": 900}
```

`workspace` is only needed for accounts that belong to several workspaces. Refresh with
`POST /api/v1/auth/refresh {"refresh_token": "..."}` (the old refresh token stops working) and
sign out with `POST /api/v1/auth/logout`.

```bash
TOKEN=<access_token>
```

## Who am I

```bash
curl -s $BASE/api/v1/me -H "Authorization: Bearer $TOKEN"
```

```json
{
  "user_id": "fcf670ee-8849-4176-9f9b-4960cd215b81",
  "email": "agent@larkspur.example",
  "full_name": "Sam Agent",
  "role": "member",
  "tenant": {"id": "10df87e9-ccb9-4342-986d-62de3cbcf707", "slug": "larkspur", "name": "Larkspur Outfitters"}
}
```

## Upload a document (admins)

```bash
curl -s $BASE/api/v1/documents -H "Authorization: Bearer $TOKEN" -F file=@returns-and-refunds.md -F title="Returns and Refunds Policy" -F access_level=all_members
```

Returns `202` with the document (`status: pending`) and its ingestion job. Poll
`GET /api/v1/ingestion-jobs/{job_id}` or `GET /api/v1/documents/{id}/jobs`:

```json
[{
  "id": "568fe14d-1c29-4aa8-b050-4fe73aa9cab2",
  "document_id": "d820774e-82b8-41e8-9eb4-f90aaa32bc3c",
  "status": "succeeded", "stage": "done", "progress": 100, "attempts": 1, "error": null,
  "created_at": "2026-11-18T18:20:41.671311Z",
  "started_at": "2026-11-18T18:20:51.377916Z",
  "finished_at": "2026-11-18T18:20:52.432577Z"
}]
```

## List documents

```bash
curl -s "$BASE/api/v1/documents?limit=2" -H "Authorization: Bearer $TOKEN"
```

```json
{
  "items": [
    {
      "id": "d820774e-82b8-41e8-9eb4-f90aaa32bc3c",
      "title": "Vendor Onboarding Notes",
      "filename": "vendor-onboarding-notes.txt",
      "media_type": "text/plain",
      "size_bytes": 1079,
      "access_level": "all_members",
      "status": "ready",
      "page_count": null,
      "chunk_count": 1,
      "embedding_model": "nomic-embed-text",
      "error": null,
      "created_at": "2026-11-18T18:20:41.669807Z",
      "updated_at": "2026-11-18T18:20:52.438123Z"
    }
  ],
  "total": 11, "limit": 2, "offset": 0
}
```

(One item shown.) The member sees 11 documents; the workspace has 12, one of them admin-only.
Filters: `status=ready|pending|processing|failed`, `q=<title text>`.

## Search (retrieval only)

```bash
curl -s $BASE/api/v1/search -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"query":"How long is the return window for Club members?","top_k":2}'
```

```json
{
  "hits": [
    {
      "chunk_id": "9a91ff46-b3d2-45b8-b513-4b47ab89716c",
      "document_id": "2900b81b-d674-4c87-b989-c610c88281d1",
      "document_title": "Larkspur Club Member Guide",
      "page": null,
      "section": null,
      "content": "... Member returns\nLarkspur Club members have a 60-day return window and free return shipping on online orders. ...",
      "similarity": 0.8124,
      "fused_score": 0.032522
    }
  ],
  "timings_ms": {"embed": 513, "search": 5}
}
```

(Content and second hit shortened.)

## Ask a question

```bash
curl -s $BASE/api/v1/chat -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' -d '{"question":"What is the return window for Larkspur Club members?"}'
```

```json
{
  "conversation_id": "57ebacda-e846-4428-ba19-d1de63131df5",
  "answer": {
    "id": "065a11d9-32cb-431e-938e-dccdddf20c9f",
    "role": "assistant",
    "content": "Larkspur Club members have a 60-day return window.",
    "citations": [
      {
        "source_id": "S1",
        "document_id": "2900b81b-d674-4c87-b989-c610c88281d1",
        "document_title": "Larkspur Club Member Guide",
        "chunk_id": "9a91ff46-b3d2-45b8-b513-4b47ab89716c",
        "page": null,
        "section": null,
        "snippet": "discount on one order during the birthday month,\n- early access to seasonal sales.\n\nMember ...",
        "similarity": 0.8725
      },
      {
        "source_id": "S2",
        "document_id": "2900b81b-d674-4c87-b989-c610c88281d1",
        "document_title": "Larkspur Club Member Guide",
        "chunk_id": "6437bfd8-dba5-463a-abbe-2fd0412f6f36",
        "page": null,
        "section": null,
        "snippet": "LARKSPUR CLUB - MEMBER GUIDE\n\nJoining\nLarkspur Club is free to join online or in any store ...",
        "similarity": 0.783
      }
    ],
    "abstained": false,
    "created_at": "2026-11-18T23:50:13.331104Z",
    "meta": {
      "retrieved": [
        {
          "source_id": "S1",
          "chunk_id": "9a91ff46-b3d2-45b8-b513-4b47ab89716c",
          "document_id": "2900b81b-d674-4c87-b989-c610c88281d1",
          "similarity": 0.8725,
          "vector_rank": 1,
          "text_rank": 2,
          "fused_score": 0.032522
        },
        {
          "source_id": "S2",
          "chunk_id": "6437bfd8-dba5-463a-abbe-2fd0412f6f36",
          "document_id": "2900b81b-d674-4c87-b989-c610c88281d1",
          "similarity": 0.783,
          "vector_rank": 3,
          "text_rank": 1,
          "fused_score": 0.032266
        }
      ],
      "candidates": 21,
      "dropped_below_threshold": 0,
      "timings_ms": {
        "embed": 84,
        "search": 8,
        "llm": 66725
      },
      "quarantined_paragraphs": 0,
      "model": "llama3.1:latest",
      "dropped_citations": [],
      "tokens": {
        "prompt": 1168,
        "completion": 44
      },
      "llm_detail_ms": {
        "load_ms": 393,
        "prompt_eval_ms": 58376,
        "eval_ms": 7823
      },
      "abstain_reason": null,
      "total_ms": 66822
    }
  },
  "question": {
    "id": "d371623b-c081-424c-a292-a79c87523420",
    "role": "user",
    "content": "What is the return window for Larkspur Club members?",
    "citations": [],
    "abstained": false,
    "created_at": "2026-11-18T23:49:06.508569Z",
    "meta": null
  }
}
```

(Snippets shortened; `meta.retrieved` lists all five passages sent to the model, two shown.
The timings are from a CPU-only model host: most of `llm` is prompt evaluation, see
`llm_detail_ms`.)

Send `conversation_id` to continue a conversation. `GET /api/v1/conversations` and
`GET /api/v1/conversations/{id}` return the user's own conversations; `GET
/api/v1/documents/{document_id}/chunks/{chunk_id}` returns the full cited passage (with the
caller's permissions).

## Errors

A member calling an admin endpoint:

```json
{"error": {"code": "forbidden", "message": "Tenant admin role required", "request_id": "b3492c613a4e4ba8bd70147bb3f06890"}}
```

A document that does not exist, belongs to another workspace, or is admin-only for a member (all
look the same, so ids cannot be probed):

```json
{"error": {"code": "not_found", "message": "Document not found", "request_id": "9bf9cb38fc844fc2a2f16373a37801ac"}}
```

Other codes: `401 unauthorized` (missing, expired or invalid token), `409 duplicate_document`,
`413` (file too large), `415 unsupported_media_type` / `content_mismatch` / `binary_text` /
`encoding`, `422` (validation), `429` (rate limit), `503` (model or queue unavailable).
