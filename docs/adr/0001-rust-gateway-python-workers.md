# ADR 0001 — Rust gateway + Python workers split

- Status: accepted
- Date: 2026-04-29
- Decider: Doudoune

## Context

Archiviste Nocilia is a public web RAG, multi-user. Two distinct concern families coexist:

- **HTTP hot path** — auth, rate limiting, validation, forwarding. Every request, latency-critical.
- **AI/data path** — ingestion, embedding, retrieval, generation, eval. Fast iteration, massive LLM ecosystem (LangChain, Ragas, sentence-transformers).

A single language would compromise one path or the other.

## Decision

Split into two services:

- **Gateway** in **Rust + Axum** — public entry point, auth, rate limit, HTTP forwarding.
- **Workers** in **Python + FastAPI** — full AI/data path.

Internal communication over HTTP (`reqwest` on the Rust side, FastAPI on the Python side). Contract formalized as OpenAPI in `specs/openapi/gateway-to-workers.yml`.

## Consequences

### Easier

- Gateway sustains high QPS without the Python GIL.
- Workers iterate on prompts/models without touching the public HTTP layer.
- Demonstrates both languages on the portfolio.

### Harder

- Two toolchains to maintain (`cargo` + `uv`).
- OpenAPI contract synced manually (mitigated by `schemathesis` in CI).
- Larger deployment surface (two Cloud Run containers).

### Cost

- ~30% extra initial complexity vs. a pure-Python monorepo.
- Justified by portfolio positioning + gateway performance ceiling.

## Alternatives considered

- **All-Python (FastAPI front + workers)** — rejected: weaker portfolio signal, lower gateway perf under load.
- **All-Rust (Axum + LangChain.rs)** — rejected: AI ecosystem in Rust too immature for Ragas, sentence-transformers, etc.
- **Internal gRPC instead of HTTP** — rejected: conceptual overhead, no measurable gain at this scale, OpenAPI is the shared standard.

## References

- `docs/architecture.md`
- `specs/openapi/gateway-to-workers.yml`
