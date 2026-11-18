# Threat model

Scope: a single self-hosted deployment serving several workspaces (tenants) of one
organisation or of several small customers. Method: assets, actors, trust boundaries, then
threats per boundary (STRIDE-style) with the control and how it is verified.

## Assets

- Document text and passages (the most sensitive asset).
- Answers and conversation history (may quote documents).
- Credentials: password hashes, refresh tokens, the JWT secret, database passwords, an optional
  OpenAI key.
- Integrity of answers (users act on them).
- Audit trail.

## Actors

| Actor | Can | Trust |
| --- | --- | --- |
| Anonymous internet user | Reach the login page and API | None |
| Member of workspace A | Read members' documents in A, ask questions | Authenticated, low privilege |
| Admin of workspace A | Upload and delete documents in A, manage A's members | Authenticated, trusted within A only |
| Member/admin of workspace B | Same, in B | Must never reach A's data |
| Author of an uploaded document | Controls document text | Untrusted content, even if uploaded by an admin |
| Operator | Runs the containers and holds the secrets | Fully trusted |
| Model provider | Receives passages and questions | Ollama: local, trusted. OpenAI: external, opt-in |

## Trust boundaries

1. Browser to nginx (HTTP; TLS terminates in front in production).
2. nginx to API (internal network).
3. API/worker to PostgreSQL and Redis (internal network, no published ports).
4. API/worker to the model host (HTTP; may cross hosts).
5. Document text to the language model (data that may contain instructions).

## Threats and controls

| # | Threat | Control | Verified by |
| --- | --- | --- | --- |
| T1 | Credential stuffing / brute force | Argon2id, per-IP and per-email login limits, generic errors, timing equalised for unknown users | Unit tests, integration test (rate limit), browser test (limit hit and waited out) |
| T2 | Stolen refresh token replay | Rotation on every use, hashed storage, family revocation on reuse | Integration test |
| T3 | Forged or tampered access token | HS256 with secret >= 32 chars in production, audience/issuer/type checks, `alg: none` rejected | Unit tests |
| T4 | Member performs admin actions | Role read from the database per request; admin-only routes | Integration permission matrix, browser test |
| T5 | Cross-tenant read via guessed ids | Tenant filter in services, forced RLS, 404 for foreign ids | Integration tests (RLS active), browser test, evaluation (0 cross-tenant citations) |
| T6 | Cross-tenant leakage through retrieval or prompt | Tenant, status, model and access filters inside the ranking SQL; RLS | Integration tests, evaluation isolation questions (see the benchmark report) |
| T7 | Member reads admin-only document via search or citation | Access level enforced in SQL and in passage lookup | Integration tests, evaluation (0 restricted citations) |
| T8 | Indirect prompt injection in a document | Paragraph quarantine, fenced sources, data-not-instructions rule, JSON schema, no tools, citation validation | Unit and integration tests; evaluation injection category (see report) |
| T9 | Model invents sources or facts | Citation validation drops unknown ids; answers without a valid citation abstain; similarity gate; temperature 0 | Unit and integration tests; evaluation citation accuracy and abstention |
| T10 | Malicious upload (zip bomb, executable, malformed PDF) | Extension + signature checks, size and page limits, encrypted PDFs rejected, parsing in a read-only, capability-less worker | Unit and integration tests, browser test |
| T11 | XSS via answer or document text | React text rendering, strict CSP, no inline scripts, self-hosted font | Header checks, manual review |
| T12 | Secrets in git, logs or images | Git-ignored `secrets/`, Docker secrets, log redaction, gitleaks in CI | Unit tests, gitleaks run (clean, one documented false positive) |
| T13 | Vulnerable dependencies or base images | Hash-pinned dependencies, digest-pinned images, pip-audit, npm audit, Trivy | CI jobs |
| T14 | Database or Redis exposed on the network | Internal Docker network, no published ports | `compose.yaml` |
| T15 | Denial of service through expensive questions | Per-user chat and search limits, question length limit, model timeouts | Integration test (limits) |

## Residual risks (accepted or out of scope)

- **Admins are trusted within their workspace.** An admin can upload a document crafted to
  steer answers with instructions the quarantine patterns do not recognise. Members see the
  cited passage and can check it, but the risk is not eliminated.
- **Pattern-based quarantine** can produce false positives (a policy that quotes an attack
  example) and false negatives (rephrased instructions).
- **No malware scanning** of uploads; nothing is executed, but files are stored until ingestion
  finishes.
- **Single shared database.** Isolation relies on RLS and application filters, not on separate
  databases per tenant.
- **No SSO/MFA** in this version; operators who need it should put an identity-aware proxy in
  front.
- **The OpenAI provider sends passages to a third party**; it is off by default.
- **Rate limits are per process window in Redis** and do not stop a distributed attack.
- **Denial of service on the model host:** CPU inference is slow (about 50 s per answer in the
  benchmark), so a handful of concurrent users can queue the model.
