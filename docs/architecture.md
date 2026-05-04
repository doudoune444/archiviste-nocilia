# Architecture — Archiviste Nocilia

## High-level diagram

```
┌─────────────┐
│   client    │  (web app, curl, ChatGPT plugin, etc.)
└──────┬──────┘
       │ HTTPS
       ▼
┌──────────────────────────────────────┐
│         Gateway (Rust, Axum)         │
│  - JWT auth, tier resolution         │
│  - Rate limit per user_tier          │
│  - Request logging + tracing         │
│  - Forwards to workers via reqwest   │
└──────┬───────────────────────────────┘
       │ HTTP (internal, via docker network)
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

### Gateway

- **Owns**: HTTP entry, auth, rate limit, request shape validation, forwarding.
- **Does NOT own**: business logic, DB schema, LLM calls, embedding logic.
- **Why Rust**: hot path (every request), low latency target p95 < 50ms forwarding overhead, type-safe contract enforcement.

### Workers

- **Owns**: ingestion pipeline, retrieval logic, generation prompts, eval loop, conversation logging, ticket detection.
- **Does NOT own**: HTTP entry from internet (gateway is the front), auth.
- **Why Python**: ecosystem (LangChain, Ragas, sentence-transformers, hypothesis), iteration speed on prompts.

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

1. Client sends POST `/v1/chat` to gateway with JWT.
2. Gateway validates JWT, derives `user_tier`, applies rate limit.
3. Gateway forwards to worker `/v1/retrieve` then `/v1/generate`.
4. Worker writes one append-only Markdown file per conversation to GCS at `conversations/{conversation_id}.md`.
5. Worker indexes the conversation in `conversations` table.
6. If generation detects lore gap, worker creates row in `tickets` with `conversation_id` FK.
7. Worker returns answer + cost + `lore_gap_detected` flag + optional `ticket_id`.
8. Gateway returns to client.

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

- **Local**: `docker compose up -d` (gateway, workers, postgres+pgvector, langfuse).
- **CI**: GitHub Actions (`.github/workflows/ci.yml`).
- **Prod**: Cloud Run (gateway and workers separately), Cloud SQL (postgres+pgvector), GCS bucket. Provisioned via Terraform in `infra/terraform/`.

## SLOs

- Availability: 99.0% monthly
- Latency: p95 chat round-trip < 3s
- Eval: faithfulness > 0.85, answer relevancy > 0.85 on golden set
