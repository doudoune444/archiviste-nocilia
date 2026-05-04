# Security (OWASP Top 10 + RAG-specific)

App = public web RAG. Threat surface large. Read this on EVERY ticket touching gateway/, workers/, or infra/.

## A01 — Broken Access Control

- Every gateway route declares its auth requirement explicitly (extractor `AuthUser` or marker `#[public]`).
- Authorization checks happen in the handler, never in middleware only.
- IDOR: any path or query param referring to a resource (`/conversations/{id}`) MUST verify ownership before read/write.
- No bulk endpoints without `LIMIT` clause.

## A02 — Cryptographic Failures

- TLS 1.2+ only. HSTS header `max-age=31536000; includeSubDomains; preload`.
- Passwords (if added): `argon2id` (m=19456 KiB, t=2, p=1). Never bcrypt for new code.
- JWT: pin `alg` to `RS256` or `EdDSA`. Reject `alg: none` AND reject `alg` not in allowlist.
- Random: `rand::rngs::OsRng` (Rust) / `secrets` module (Python). Never `rand::thread_rng()` for tokens.
- Encryption at rest: rely on GCS / Cloud SQL default. No app-level crypto unless ADR.

## A03 — Injection

- SQL: `sqlx` query macros only (compile-checked). No `format!` into queries.
- Shell: never spawn shell with user input. Use argv arrays per token.
- Template injection: Tera/Jinja in autoescape. Never raw blocks.
- LLM prompt injection: see "RAG-specific" below.

## A04 — Insecure Design

- Rate limit every public route: `tower_governor` (Rust) / `slowapi` (Python). Default: 60 req/min/IP.
- Idempotency keys for state-changing endpoints (POST/PUT/DELETE).
- Resource limits: max body 1 MiB, max query length 4 KiB, max upload 10 MiB.
- Timeouts on every external call (LLM, GCS, DB): 30s default, hard cap.

## A05 — Security Misconfiguration

- Debug endpoints gated by `cfg(debug_assertions)` AND env flag.
- Error responses : never leak stack traces, file paths, query strings, or DB errors. Return `{"error": "internal", "request_id": "..."}`.
- CORS: explicit allowlist of origins. NEVER wildcard with credentials.
- Default content-type: `application/json; charset=utf-8`. Set `X-Content-Type-Options: nosniff`.
- CSP : `default-src 'self'; object-src 'none'; frame-ancestors 'none'`.
- `Referrer-Policy: strict-origin-when-cross-origin`.

## A06 — Vulnerable Components

- Run `cargo deny check` on every CI build. Severity HIGH/CRITICAL = blocker.
- Run `pip-audit` on every CI build. Same threshold.
- Lock files committed (`Cargo.lock`, `uv.lock`).
- New dep above 1k LOC or any FFI: requires ADR.

## A07 — Authentication Failures

- Login throttling: 5 failed attempts / 15 min / account → backoff.
- Session tokens: 32 bytes random, stored hashed (argon2id) server-side.
- Logout invalidates server-side token (no JWT-only logout).
- MFA mandatory for admin routes.

## A08 — Software & Data Integrity

- All deps from official registries. No git URL deps.
- CI builds reproducible (lock files + `--frozen` / `--locked`).
- Webhook signatures verified (HMAC-SHA256 timing-safe compare).
- Auto-update / self-modification forbidden.

## A09 — Logging & Monitoring

- Log: request_id, user_id (if auth), route, status, latency_ms. NEVER log: passwords, tokens, full prompts containing PII, full response bodies.
- Sensitive types in code: `secrecy::Secret<String>` (Rust), `pydantic.SecretStr` (Python). These types redact in Debug / repr / log output. ANY token, password, API key, JWT, DB connection string MUST use these types.
- Failed auth + IDOR attempts go to alerting channel.
- Retention: app logs 30 days, audit logs 1 year.

## A10 — Server-Side Request Forgery (SSRF)

- Any code path fetching a user-supplied URL MUST validate against an allowlist OR resolve the host AND reject:
  - Private CIDRs: `10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`, `127.0.0.0/8`, `169.254.0.0/16`, `fc00::/7`, `::1`.
  - Cloud metadata: `169.254.169.254`, `metadata.google.internal`, `metadata.aws.internal`.
- DNS rebinding mitigation: resolve once, fetch by IP, with `Host:` header set explicitly.
- Redirect policy: none, or limit to 3 hops + re-validate each.
- Use `reqwest::ClientBuilder::resolve_to_addrs()` or pinned IP. Don't trust libc DNS in handler.

## RAG-specific threats

### Prompt injection (input side)

- User query containing instruction-override patterns ("IGNORE PRIOR…"): input firewall pattern-detect + reject OR sandbox via system prompt structure (XML tags, role separation).
- Retrieved chunks containing injected instructions (poisoned doc): treat all retrieved content as `untrusted_data` zone in prompt template. Never let retrieved text reach `system` role.

### Embedding poisoning (ingestion side)

- Ingested doc with adversarial content (zero-width chars, prompt-shaped markdown): normalize (`unicodedata.normalize('NFKC')`), strip control chars, length cap.
- Reject docs with embedding cosine ≥ 0.99 to a known-bad cluster (operator curated list).

### Output sanitization

- LLM output rendered to web: HTML-escape. Never inject raw HTML from LLM.
- LLM output containing markdown links: validate scheme allowlist (`http`, `https`, `mailto`).
- LLM output containing code blocks: render inside fenced `<pre><code>` only, no auto-execute.

### Conversation persistence

- GCS bucket: uniform bucket-level access. NO public ACL. Service account has `roles/storage.objectAdmin` on this bucket only.
- Markdown filenames: derived from `uuid` only. Never user-controlled (path traversal).

## Forbidden patterns (auto-fail review)

- `unwrap()` / `expect()` on user-derived input
- `String::from_utf8_unchecked` / any `unsafe` block without SAFETY comment
- Dynamic code evaluation, unsafe deserialization (Python `pickle`, unsafe YAML), shell-mode subprocess with user input
- Hardcoded credentials, even "for tests" (use fixtures + env var override)
- `verify=False` on any TLS call
- CORS wildcard origin combined with `allow_credentials: true`
- JWT decode without signature verification, audience check, or alg pin
- Direct format string into SQL / shell / template
