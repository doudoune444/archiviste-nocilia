# Archiviste Nocilia

> Public web RAG application — **Rust gateway + Python workers** — built with a strict **agent-driven workflow** (Claude Code) and an **enterprise-grade security baseline**.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
![Status](https://img.shields.io/badge/status-bootstrap-orange)
![Rust](https://img.shields.io/badge/rust-1.95%2B-orange?logo=rust)
![Python](https://img.shields.io/badge/python-3.12%2B-blue?logo=python)
![Postgres](https://img.shields.io/badge/postgres-16%20%2B%20pgvector-336791?logo=postgresql)

This repository is a **showcase / portfolio project**. It demonstrates how to ship an LLM-powered application with the rigor expected in regulated industries (SME / enterprise / compliance-sensitive deployments).

---

## Why this project exists

Three motivations, in order:

- **Ship something production-ready with the rigor of regulated industries** — threat model, default-deny security, strict linting, eval gates, deployed on GCP (Cloud Run, Cloud SQL, Secret Manager) with Terraform-managed infra. Treat a solo side project like a compliance-sensitive production deployment to see what it actually costs.
- **Test a new way of working with AI** — drive the whole build through Claude Code with narrow, well-scoped agents and hard rules, to enforce cleanliness and avoid the sloppy code an LLM produces when given free rein.
- **Build the tool I actually need** — a RAG over my own world-building corpus, queryable in natural language, that also surfaces *lore gaps*: missing cases, contradictions, and inconsistencies get filed as tickets I can act on. The engineering rigor above is the showcase; the corpus is the genuine use case.

---

## Stack

| Layer | Technology |
|---|---|
| **Gateway** | Rust 1.95 · Axum 0.8 · Tokio · sqlx · tower-http |
| **Workers** | Python 3.12 · FastAPI · LangChain · pgvector · Sentence-Transformers |
| **Database** | PostgreSQL 16 with `vector` extension |
| **Storage** | Google Cloud Storage (conversations as Markdown) |
| **Observability** | Langfuse · OpenTelemetry · Prometheus · structlog |
| **Eval** | Ragas (faithfulness, answer relevancy, context precision/recall) |
| **Infra** | Cloud Run · Cloud SQL · Secret Manager · Terraform (deferred) |
| **CI/CD** | GitHub Actions · Dependabot |

---

## Architecture at a glance

- **Edge** : Cloudflare (DDoS protection, WAF, bot management)
- **Gateway** (public) : Rust Axum on Cloud Run — auth, rate limiting, request routing
- **Workers** (internal-only) : Python FastAPI on Cloud Run — ingestion, retrieval, generation. Reached only via HMAC-authenticated calls from the Gateway.
- **Persistence** : PostgreSQL 16 with pgvector (Cloud SQL, private IP) for users, sessions, embeddings ; GCS for conversation transcripts (Markdown, namespaced per user, versioned)
- **External services** : LLM provider API (Anthropic / OpenAI), Langfuse for LLM tracing, GCP Secret Manager for credentials

---

## Engineering principles

### 1. Security is non-negotiable

- **Threat model** in STRIDE methodology — [`specs/threat-model.md`](specs/threat-model.md)
- **OWASP Top 10 + RAG-specific threats** — [`.claude/rules/security.md`](.claude/rules/security.md) (prompt injection, embedding poisoning, output XSS, SSRF via doc URLs)
- **Pre-commit gates** — `gitleaks`, `cargo-deny`, `pip-audit`, `redocly lint`, `ruff`, `clippy`, `mypy --strict`
- **Default-deny permissions** in [`.claude/settings.json`](.claude/settings.json) — agent file access scoped to working dirs, Bash whitelist, no destructive git ops
- **Sensitive types enforced** — `secrecy::Secret<T>` (Rust), `pydantic.SecretStr` (Python). Logging redact-by-default.
- **No secrets in repo** — GCP Secret Manager in production, `.env.example` only as template

### 2. Observability is built-in, not bolted on

LLM calls go through Langfuse (with PII redaction before push). Application metrics through Prometheus + OpenTelemetry. Structured JSON logs everywhere.

### 3. Eval is a first-class CI gate

A Ragas eval workflow runs on every PR touching the RAG path or the golden Q/A set. Regressions vs `eval/baseline.json` block merge.

---

## Quick start

**Prerequisites :** Rust 1.95+, Python 3.12+, [uv](https://docs.astral.sh/uv/), Docker, Docker Compose.

```bash
# 1. Bring up the full local stack (postgres, langfuse, gateway, workers)
docker compose up -d

# 2. Verify gateway
curl http://localhost:8080/healthz

# 3. Verify workers
curl http://localhost:8000/healthz
```

For granular development (running services on host, hot reload, etc.), see [`docs/runbook/`](docs/runbook/).

---

## Repository layout

```
.claude/         Rules, skills, commands, hooks, permissions
gateway/         Rust Axum service — handlers, auth, rate limit, OpenAPI client
workers/         Python FastAPI service — ingestion, retrieval, generation, eval
specs/           Contract & eval sources — openapi/, golden_qa.jsonl, properties.md, threat-model.md
docs/            runbook/, agents/ (skill config), load-test report
eval/            Ragas runner, baseline metrics, seed corpus
infra/           Dockerfiles, docker-compose, Terraform (deferred)
migrations/      SQL migrations (human-only, sqlx-checked)
scripts/         Setup helpers
.github/         CI/CD, Dependabot, issue and PR templates
```

---

## Documentation

| Document | Purpose |
|---|---|
| [`CLAUDE.md`](CLAUDE.md) | Project memory — conventions, sources of truth |
| [`specs/threat-model.md`](specs/threat-model.md) | STRIDE matrix across all components |
| [`docs/runbook/`](docs/runbook/) | Local development, incidents, deployment |
| [`SECURITY.md`](SECURITY.md) | Vulnerability disclosure policy |

---

## Status

Active development — tracked work lives in GitHub Issues.

This is a solo project. I am not currently accepting external contributions, but the workflow and the code are public for review.

---

## License

[MIT](LICENSE) — Copyright (c) 2026 Doudoune (Baptiste Herbecq)

---

## Contact

- Author : **Doudoune** (Baptiste Herbecq)
- Email : `baptiste.herbecq@gmail.com`
- Security disclosure : see [`SECURITY.md`](SECURITY.md)
