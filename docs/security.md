# Security

This document describes the controls that exist in the code and how they were tested. It is not
a certification or an audit; see [threat-model.md](threat-model.md) for what is and is not
covered.

## Authentication

- Passwords are hashed with Argon2id (time cost 3, 64 MiB, parallelism 4). Unknown accounts are
  checked against a dummy hash so response time does not reveal whether an email exists. Hashes
  are upgraded on login when the parameters change. New passwords need at least 12 characters.
- Access tokens are HS256 JWTs valid for 15 minutes, with issuer, audience, type and a unique id
  checked on every request. `alg: none`, wrong audience or type and tampered tokens are rejected
  (unit tests).
- Refresh tokens are random 256-bit values stored only as SHA-256 hashes, valid for 7 days and
  rotated on every use. Presenting an already-rotated token revokes the whole token family and is
  audited as `auth.refresh_reuse` (integration test).
- Login failures return one generic message for wrong email, wrong password and wrong workspace,
  and are rate limited per client IP and per email (10 per minute each by default).
- The web app keeps the access token in memory and the refresh token in `sessionStorage` (cleared
  on logout and when the tab closes). This trades persistence for a smaller XSS blast radius than
  `localStorage`; the strict CSP below is the main XSS control.

## Authorisation (RBAC)

Two roles per workspace: **Tenant Admin** (upload, delete, change document access, manage
members, read the audit log) and **Member** (read documents allowed to members, ask questions,
manage their own conversations). The role is read from the membership row on every request, so
a role change or removal takes effect immediately, not when a token expires. Denied admin actions
are audited as `authz.admin_required`. The permission matrix is covered by integration and
browser tests.

Documents have an access level: `all_members` or `admins_only`. The level is enforced inside the
retrieval SQL, in every document and passage endpoint, and in citation lookups, so a member can
neither search, open nor be cited a restricted document (integration tests, browser test, and 0
leaks in the evaluation).

## Tenant isolation

1. Every tenant-owned table has `tenant_id`; services always filter on the caller's tenant,
   which comes from the membership, never from client input.
2. PostgreSQL Row-Level Security is **forced** on documents, uploads, jobs, chunks,
   conversations, messages and audit events. The application connects as `rag_app`, a role that
   is not the table owner and has no `BYPASSRLS`. Each transaction sets
   `app.tenant_id` with `set_config(..., true)` (transaction-local); with no tenant set, policies
   match no rows.
3. Retrieval repeats the tenant filter in its SQL, so even a bug in session binding would return
   nothing rather than another tenant's text.
4. Citations returned by the model are accepted only if they name a passage that was sent.

Tests: integration tests read and write across tenants with RLS active and expect nothing (and
check that the application role cannot bypass it); API tests expect 404 for every
cross-tenant id; browser tests repeat this through the UI; the evaluation recorded 0
cross-tenant citations.

## Prompt injection and output handling

Documents and questions are untrusted. Defences, none assumed complete on its own:

- Source passages are fenced with markers the text cannot forge (`<<<` and `>>>` inside text are
  neutralised).
- **Quarantine:** paragraphs that look like instructions to an AI system ("ignore previous
  instructions", "reveal the system prompt", "from now on ... answer") are replaced with a short
  notice before the passage reaches the model. In the first evaluation run a warning alone did
  not stop the model from repeating an injected payload; see the benchmark report.
- The system prompt says sources are data, not instructions; the model has no tools and must
  return a fixed JSON schema.
- Citations are validated; answers without a valid citation become abstentions.
- The UI renders answers as text (React escaping), never as HTML.

Known gap: pattern-based quarantine can be evaded by rephrased instructions. A determined
attacker who can upload documents (an admin) can still try to steer answers; see the threat
model.

## Uploads

Only `.pdf`, `.txt`, `.md` and `.markdown`, up to 20 MB (configurable). PDFs must start with the
PDF signature; text files must be UTF-8, contain no NUL bytes and must not start with a known
binary signature (ZIP/Office, executables, archives, PDF). Encrypted PDFs, PDFs over the page
limit and files without extractable text are rejected with a clear error. Filenames are
sanitised. Nothing in a document is executed; PDF parsing uses pypdf in the worker, which runs
with a read-only filesystem and no capabilities.

## Transport and headers

- nginx sends `Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self';
  ... frame-ancestors 'none'`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`,
  `Referrer-Policy: no-referrer`, `Permissions-Policy` and `Cross-Origin-Opener-Policy`. The font
  is self-hosted so the CSP needs no third-party origin.
- API responses carry `Cache-Control: no-store`, `X-Request-ID`, nosniff, DENY, no-referrer and a
  `default-src 'none'` CSP (except the OpenAPI docs page).
- CORS is off unless `RAG_CORS_ORIGINS` is set; the SPA and API share one origin.
- TLS is expected to terminate in front of the web container (reverse proxy or load balancer);
  see [deployment.md](deployment.md).

## Secrets

Secrets live in `./secrets/*` (git-ignored, generated by `scripts/init-secrets.sh`) and reach
containers as Docker secrets (`*_FILE` variables). Logs pass through a redaction filter for
bearer tokens, JWTs, `password=`, API keys and similar patterns (unit tests). gitleaks scans the
full history in CI.

## Audit log

Append-only for the application role (no UPDATE or DELETE grants): logins and failures, logouts,
refresh-token reuse, denied admin actions, uploads, deletions, access-level changes and member
changes, with actor, target, IP and request outcome. Document text, passwords and tokens are
never written to it. Admins read it at `GET /api/v1/tenants/current/audit-events`.

## Supply chain

Python dependencies are pinned with hashes and installed with `--require-hashes`; npm uses a
lock file with integrity hashes; base images are pinned by digest; GitHub Actions are pinned by
commit SHA. CI runs pip-audit, `npm audit` (production dependencies) and Trivy on both images.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting on this repository rather than a public
issue.
