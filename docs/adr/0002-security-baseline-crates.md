# ADR 0002 — Security baseline crates / libs

- Status: accepted
- Date: 2026-04-29
- Decider: Doudoune

## Context

App = public web RAG. OWASP A07 (auth failures), A04 (insecure design — no rate limit), A02 (crypto failures) account for the majority of post-launch incidents on Cloud Run apps. We codify the minimal security stack here so every implementer reaches for the same approved primitive instead of inventing.

## Decision

### Rust gateway

| Concern | Crate | Version | Why |
|---|---|---|---|
| Password hashing | `argon2` | `0.5+` | OWASP 2024 recommended. `bcrypt` deprecated for new projects. |
| Rate limit | `tower_governor` | `0.4+` | Per-IP token bucket as Tower layer. Idiomatic with Axum. |
| CORS | `tower-http::cors::CorsLayer` | (workspace) | Explicit allowlist, no wildcard with credentials. |
| Security headers | `tower-http::set_header` + `tower-http::cors` | (workspace) | CSP, HSTS, X-Content-Type-Options, Referrer-Policy. |
| Input validation | `validator` (derive) + `garde` (alt) | `0.18+` | Declarative struct validation, attached to handler extractor. |
| JWT | `jsonwebtoken` | `9+` | Pin alg via `Algorithm` enum, reject `None`. |
| Secret type | `secrecy` | `0.10+` | Redacted Debug. Wraps tokens / passwords / connection strings. |
| HTTP client (SSRF-safe) | `reqwest` + `hickory-resolver` | latest | `resolve_to_addrs()` for DNS pinning, redirect policy=none. |
| CSRF (if cookies) | `axum-csrf` or custom double-submit | latest | Required if any session cookie. |

### Python workers

| Concern | Lib | Version | Why |
|---|---|---|---|
| Password hashing | `argon2-cffi` | `23+` | Same rationale. |
| Rate limit | `slowapi` | `0.1.9+` | FastAPI-native. |
| Input validation | `pydantic` strict mode | `2+` | `model_config = ConfigDict(strict=True)`. |
| Secret type | `pydantic.SecretStr` | (pydantic v2) | `__repr__` redaction. |
| JWT | `python-jose[cryptography]` | `3.3+` | Algo pin via `algorithms=["RS256"]`. |
| HTTP client (SSRF-safe) | `httpx` + custom transport with IP allowlist | `0.27+` | Resolve once, fetch by IP, validate. |
| HTML escape (LLM output) | `markupsafe` | `2.1+` | If output rendered in template. |
| Webhook HMAC | `hmac` (stdlib) + `hmac.compare_digest` | stdlib | Timing-safe compare. |

### Forbidden

- `bcrypt` (Rust + Python) — deprecated for new code.
- `pyjwt` < 2.4 — known alg confusion bugs.
- `requests` for any user-supplied URL — no built-in SSRF guard.
- `python-jose` `algorithms=["*"]` — alg confusion.
- Any TLS lib with `verify=False` toggle in prod path.

## Consequences

### Positive
- New ticket touching auth/HTTP gets a clear "use X" answer.
- Reviewer has a concrete checklist.
- Audit (cargo-deny / pip-audit) enforces version floor automatically.

### Negative
- Coupling to specific crates. If `tower_governor` is abandoned, migration cost.
- Some crates (`secrecy`, `validator`) add boilerplate to every handler. Net win vs leaks.

### Neutral
- ADR re-evaluated each year or on major Rust/FastAPI release.

## Alternatives considered

- **Hand-rolled rate limit**: rejected, classic source of off-by-one + race conditions.
- **`bcrypt`**: rejected, OWASP marks as "no longer recommended" for new projects (2024 cheatsheet).
- **No JWT alg pin (`decode` defaults)**: rejected, alg confusion = forge tokens.

## References

- OWASP Cheat Sheet: Password Storage, JWT, REST Security
- RUSTSEC advisories monitored via `cargo deny`
- Pyca/cryptography audits
