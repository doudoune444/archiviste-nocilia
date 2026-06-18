# Architecture — Archiviste Nocilia

## High-level diagram

```
┌─────────────┐
│   browser   │  (archiviste.nocilia.fr via Cloudflare)
└──────┬──────┘
       │ HTTPS (Cloudflare terminates TLS)
       ▼
┌──────────────────────────────────────┐
│   Frontend (Next.js, Cloud Run)      │  ← PLATFORM-004: new public origin
│  - App Router RSC pages              │
│  - bff-proxy: sole caller of gateway │
│  - nonce-based CSP middleware        │
│  - allUsers IAM invoker              │
└──────┬───────────────────────────────┘
       │ HTTPS + Authorization: Bearer <ID token>
       │ (gateway IAM-gated: archiviste-runtime SA only)
       ▼
┌──────────────────────────────────────┐
│         Gateway (Rust, Axum)         │  ← not browser-reachable (PLATFORM-004 AC-2)
│  - JWT auth, tier resolution         │
│  - Rate limit per user_tier          │
│  - Request logging + tracing         │
│  - Forwards to workers via reqwest   │
└──────┬───────────────────────────────┘
       │ HTTPS + Authorization: Bearer <ID token>
       │ (workers IAM-gated: archiviste-runtime SA only)
       ▼
┌──────────────────────────────────────┐
│      Workers (Python, FastAPI)       │
│  - /v1/retrieve (vector / hybrid)    │
│  - /v1/generate (LangChain)          │
│  - /v1/ingest (admin)                │
│  - conversation_logger → GCS         │
│  - lore-gap detection → tickets      │
└──────┬─────────────┬─────────────────┘
       │             │
       ▼             ▼
┌──────────┐   ┌─────────────────┐
│ Postgres │   │ GCS bucket      │
│ pgvector │   │ archiviste-     │
│          │   │ conversations   │
│ - chunks │   │ - 1 .md / conv  │
│ - embeds │   └─────────────────┘
│ - convs  │
│ - tickets│
└──────────┘
```

## Service boundaries

### Frontend (PLATFORM-004)

- **Owns**: public web origin, browser-facing HTML/CSS/JS, BFF proxy to gateway.
- **Does NOT own**: auth logic, rate limiting, DB, LLM calls.
- **Why Cloud Run**: scale-to-zero (near-zero idle cost), consistent with gateway/workers topology.
- **IAM**: `allUsers` run.invoker (public browser traffic). Attaches SA ID token on outbound gateway calls.

### Gateway

- **Owns**: HTTP entry from the frontend, auth, rate limit, request shape validation, forwarding.
- **Does NOT own**: business logic, DB schema, LLM calls, embedding logic.
- **Why Rust**: hot path (every request), low latency target p95 < 50ms forwarding overhead, type-safe contract enforcement.
- **IAM**: `archiviste-runtime` SA run.invoker only (PLATFORM-004 AC-2). Not directly browser-reachable.

### Workers

- **Owns**: ingestion pipeline, retrieval logic, generation prompts, eval loop, conversation logging, ticket detection.
- **Does NOT own**: HTTP entry from internet (gateway is the front), auth.
- **Why Python**: ecosystem (LangChain, Ragas, sentence-transformers, hypothesis), iteration speed on prompts.
- **IAM**: `archiviste-runtime` SA run.invoker only (SEC-006).

## Data model

See [data-model.md](data-model.md) for full schema.

Key tables:
- `documents` — uploaded source documents
- `chunks` — chunked + embedded segments (pgvector column)
- `conversations` — index over GCS-stored Markdown conversations
- `tickets` — lore-gap detected during a conversation, FK → conversations
- `embeddings_jobs` — async ingestion queue

## Key flows

### Conversation flow

1. Browser requests page from Frontend (Cloud Run, `archiviste.nocilia.fr`).
2. Frontend RSC page calls `bff-proxy.forward()` which attaches a metadata ID token.
3. Gateway validates ID token (IAM), validates JWT, derives `user_tier`, applies rate limit.
4. Gateway forwards to worker `/v1/retrieve` then `/v1/generate`.
5. Worker writes one append-only Markdown file per conversation to GCS at `conversations/{conversation_id}.md`.
6. Worker indexes the conversation in `conversations` table.
7. If generation detects lore gap, worker creates row in `tickets` with `conversation_id` FK.
8. Worker returns answer + cost + `lore_gap_detected` flag + optional `ticket_id`.
9. Gateway returns to frontend; frontend renders result to browser.

### Ingestion flow

1. Admin triggers POST `/admin/ingest` with document URL or upload.
2. Worker chunks, embeds, writes to `documents` and `chunks` tables.
3. Eval set is re-run if affected (manual gate via `/eval`).

## Observability

- **Langfuse**: every LLM call traced with prompt, response, latency, cost, model version.
- **OpenTelemetry**: spans across gateway → worker → DB.
- **Logs**: structured JSON, shipped to GCP Logging in prod.
- **Metrics**: Prometheus exposition on `/metrics`.

## Deployment

- **Local**: `docker compose up -d` (gateway, workers, postgres+pgvector, langfuse). Frontend: `cd frontend && npm run dev`.
- **CI**: GitHub Actions (`.github/workflows/ci.yml`).
- **Prod**: Cloud Run (frontend, gateway, workers separately), Cloud SQL (postgres+pgvector), GCS bucket. Provisioned via Terraform in `infra/terraform/`.

## SLOs

- Availability: 99.0% monthly
- Latency: p95 chat round-trip < 3s
- Eval: faithfulness > 0.85, answer relevancy > 0.85 on golden set
