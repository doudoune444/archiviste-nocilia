# Security — project invariants

App = public web RAG. Read on EVERY ticket touching `gateway/`, `workers/`, `infra/`.

This file does **not** restate generic OWASP — the model applies that by default
(parameterized SQL, no `alg:none`, HTML-escape, no debug endpoints in prod, no
wildcard-CORS-with-credentials). It codifies three things the model will *not*
reliably produce on its own: **our pinned decisions**, the **RAG-specific threats**,
and the **auto-fail list** that makes review deterministic.

## Pinned decisions (non-negotiable, not defaults)

### Access control
- Every route declares auth explicitly: `AuthUser` extractor OR `#[public]` marker. Authorization in the handler, never middleware-only.
- IDOR: any resource id in path/query (`/conversations/{id}`) → verify ownership before read/write.
- No bulk/list endpoint without a `LIMIT`.

### Crypto & auth
- Passwords: `argon2id` only, `m=19456 KiB, t=2, p=1`. Never bcrypt.
- JWT: pin `alg` to `EdDSA` or `RS256`. Reject `alg:none` and any alg outside the allowlist. Verify signature + `aud`.
- Randomness for tokens/secrets: `OsRng` (Rust) / `secrets` (Python). Never `thread_rng()`.
- Session tokens: 32 bytes random, stored hashed (`argon2id`), server-side. Logout invalidates server-side (no JWT-only logout). MFA on admin routes.
- Login throttle: 5 fails / 15 min / account → backoff.

### Secret types (mandatory)
- Any token / password / API key / JWT / DB connection string → `secrecy::Secret<String>` (Rust) / `pydantic.SecretStr` (Python). They redact in Debug/repr/logs.
- Production secrets via GCP Secret Manager. No `os.getenv("KEY", "default")` fallback.

### Limits & timeouts (hard caps)
- Rate limit every public route: `tower_governor` (Rust) / `slowapi` (Python). Default 60 req/min/IP.
- Body ≤ 1 MiB · query ≤ 4 KiB · upload ≤ 10 MiB.
- Every external call (LLM, GCS, DB): 30 s timeout, hard cap.
- Idempotency keys on state-changing POST/PUT/DELETE.

### Response & headers
- Errors never leak stack / path / query / DB error. Return `{"error":"internal","request_id":"..."}`.
- CORS: explicit origin allowlist. Never wildcard with credentials.
- Set `Content-Security-Policy: default-src 'self'; object-src 'none'; frame-ancestors 'none'`, `Strict-Transport-Security: max-age=31536000; includeSubDomains; preload`, `X-Content-Type-Options: nosniff`, `Referrer-Policy: strict-origin-when-cross-origin`.

### Dependencies & integrity
- CI: `cargo deny check` + `pip-audit`. HIGH/CRITICAL = blocker. Lock files committed, builds `--locked` / `--frozen`.
- Official registries only. No git-URL deps. New dep > 1k LOC or any FFI → flag for explicit human approval in the PR (never silent).
- Webhook signatures: HMAC-SHA256, timing-safe compare.

### Logging
- Log: request_id, user_id (if auth), route, status, latency_ms. NEVER log: passwords, tokens, PII-bearing prompts, full response bodies.
- Failed auth + IDOR attempts → alerting channel. Retention: app logs 30 d, audit logs 1 y.

## SSRF (concrete — our infra)

Any code path fetching a user-supplied URL MUST:
- Validate against an allowlist OR resolve the host and reject private CIDRs (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`, `169.254.0.0/16`, `fc00::/7`, `::1`) AND cloud metadata (`169.254.169.254`, `metadata.google.internal`, `metadata.aws.internal`).
- Resolve once, fetch by IP, set `Host:` explicitly (DNS-rebinding). Redirects: none, or ≤ 3 hops re-validated each.
- Use `reqwest::ClientBuilder::resolve_to_addrs()` / pinned IP. Don't trust libc DNS in the handler.

## RAG-specific threats (our delta — the model won't volunteer these)

### Prompt injection
- User query with override patterns ("IGNORE PRIOR…") → input firewall reject OR sandbox via role separation (XML tags).
- Retrieved chunks = `untrusted_data` zone in the prompt template. **Never** let retrieved text reach the `system` role.

### Embedding poisoning (ingestion)
- Normalize every ingested doc: `unicodedata.normalize('NFKC')`, strip control chars, length cap (defends zero-width / prompt-shaped markdown).
- Reject docs with embedding cosine ≥ 0.99 to an operator-curated known-bad cluster.

### Output sanitization
- LLM output to web: HTML-escape, never raw HTML.
- Markdown links: scheme allowlist (`http`, `https`, `mailto`).
- Code blocks: fenced `<pre><code>` only, no auto-execute.

### Conversation persistence
- GCS: uniform bucket-level access, NO public ACL. Service account = `roles/storage.objectAdmin` on this bucket only.
- Markdown filenames derived from `uuid` only. Never user-controlled (path traversal).

## Forbidden patterns (auto-fail review)

- `unwrap()` / `expect()` on user-derived input
- `String::from_utf8_unchecked` / any `unsafe` block without a `SAFETY` comment
- Dynamic code eval; unsafe deserialization (Python `pickle`, unsafe YAML); shell-mode subprocess with user input
- Hardcoded credentials, even "for tests" (use fixtures + env override)
- `verify=False` on any TLS call
- CORS wildcard origin combined with `allow_credentials: true`
- JWT decode without signature verification, `aud` check, or alg pin
- Direct format string into SQL / shell / template
