# Changelog

All notable changes to this project will be documented here.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).
Versioning: [SemVer](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Repo scaffolding (CLAUDE.md, .claude/ agents + commands, specs/, docs/, gateway/, workers/, eval/, infra/)
- Workflow Claude Code : architect / implementer / reviewer / eval-runner / debugger sub-agents
- Slash commands : /ticket /plan /impl /review /eval /debug /ship
- ADR 0001 : split Rust gateway + Python workers
- OpenAPI contract gateway-to-workers
- Golden Q/A skeleton + property invariants table
- CI workflows : lint + test + contract + ragas eval
- pre-commit : ruff, fmt, clippy, gitleaks, conventional commits
- **FOUND-001** : minimal viable scaffold — gateway `/healthz` proxying workers `/healthz`, docker-compose dev stack (postgres + gateway + workers), integration test green, CI passing.
- **FOUND-002** : reproducible local stack + boot SLA. Adds `redis` service (auth required, persisted via `redis-data` volume, no host port), `migrator` service under `profiles: ["tools"]` running `migrations/run.sh` (versioned, transactional, gap-detecting), `make migrate` target, `.env.example`, `scripts/measure-boot.sh` writing JSON artefact, dedicated `.github/workflows/boot-sla.yml` (non-blocking), runbook section on migrations + boot baselines.
- **FOUND-002 (review fixes)** : wired migrations integration suite (`tests/migrations/run_tests.sh`) and new stack integration suite (`tests/integration/test_stack.sh`, covers AC-2 redis no-auth rejection, AC-3 persistence across restart, AC-6 migrator excluded from `up -d`) into `boot-sla.yml`. `measure-boot.sh` now parses both NDJSON and JSON-array forms of `docker compose ps --format json`. Boot-metrics JSON shape is validated in CI. Build step trimmed to local-only images (`workers`, `gateway`). Runbook documents `make migrate` as mandatory first-boot step + forbids transaction control statements in migration files.
- **ING-003** : conversation logger workers (`POST /v1/conversations/{id}/messages`, GCS Markdown append-only avec `ifGenerationMatch` + retries déterministes 50/200/800 ms, index Postgres `conversations`, fake-gcs-server pinné `1.49` sous `profiles: ["tools"]`, `GCS_BUCKET` fail-fast au boot via pydantic-settings).
- **FOUND-003** : schema walking skeleton (`users`, `documents`, `chunks`, `conversations`, `tickets`, `query_log`) + HNSW cosine index on `chunks.embedding` (1024-dim) + sentinel anonymous user. Migration runner gains a `-- description: <text>` first-line directive (filename slug fallback). Integration tests cover AC-1..AC-16 against pgvector/pgvector:pg16.
- **GEN-001** : POST /v1/generate mode canon (LLM wrapper config-driven multi-provider Mistral default, citations parser permissive + filter, prompt 3-zones avec `<no_archives_found/>` marker, query_log + conversation log best-effort, timeout dur 30 s, prompt-injection sanitize-prefix).
- **GEN-002** : gateway `POST /v1/chat` forwarder — validates body (query ≤ 4 KiB, conversation_id UUID, body ≤ 1 MiB), generates `UUIDv4` request_id, forwards to workers `/v1/generate` with sentinel `user_id`/`user_tier`, passthrough response ≤ 256 KiB (streamed cap, no full-buffer DoS), uniform error envelopes (400/503/502/504), structured log per request without raw query, connect timeout 5 s / request timeout 35 s. LOC budget note: +15 LOC over cap 300 — `attach_request_id` middleware (decision R2, ~25 LOC) + verbose error-envelope types (~30 LOC) account for the overrun; no split warranted given the changes are functionally inseparable.
- **ING-001** : ingest CLI `python -m archiviste_workers.ingest --path lore/` walks `*.md`, parses YAML frontmatter (title required, `tags`, `access_tier` ∈ {public, members, author_only}), normalizes body NFKC + strips C0 controls, hashes (SHA-256), chunks via `RecursiveCharacterTextSplitter` (bge-m3 tokenizer, 512/64), embeds with `BAAI/bge-m3` (1024-dim, batch via `EMBED_BATCH_SIZE`), and UPSERTs `documents` + `chunks` idempotently (per-file transaction, hash-based skip/update). Adds `lore/sample/` seed (archiviste, scriptorium) and isolated test fixtures under `workers/tests/fixtures/lore/`.
- **RET-001 (review fixes)** : production lifespan now uses `db.create_pool` so the pgvector codec is registered (was raw `asyncpg.create_pool` — would fail to encode 1024-dim list as `vector` on first call); OpenAPI `GenerateRequest.contexts.items.$ref` repointed from removed `Chunk` to `RetrievedChunk` (full-document lint clean); retrieve handler now offloads the synchronous bge-m3 encode via `asyncio.to_thread` so concurrent requests don't serialize on the event loop. Added integration regression `tests/test_main_lifespan.py` asserting the lifespan pool round-trips a 1024-dim `vector`.
  - **CI fixes** : `boot-sla.yml` and `ci.yml` `contract` job now `CREATE EXTENSION IF NOT EXISTS vector` against the postgres service before workers start, so the new lifespan codec registration doesn't crash boot. `tests/test_retrieve_integration.py::test_database_unavailable_returns_503` now mirrors the conftest skip-gate (try/except `OSError` → `pytest.skip`) so the AC-14 test is gated on Postgres availability like sibling tests.
- **RET-001** : workers expose `POST /v1/retrieve` — embeds the query with bge-m3 (loaded once at lifespan startup), runs a single ACL-filtered cosine top-K SQL on `chunks` via the existing asyncpg pool (`<=>` operator, `WHERE access_tier = ANY(...)`, deterministic tie-break on `chunk_id`), and returns ordered chunks with `embedding_ms` / `search_ms` timings. Hard limits: `query` ≤ 4 KiB UTF-8, `top_k` ∈ [1, 20], SQL timeout 5 s. Errors map to `400 invalid_{request,query,top_k,user_tier}` and `503 {embedder,database}_unavailable`. Single redacted JSON log per request (no `query`/`text`/`embedding`/DB error leak). OpenAPI `/v1/retrieve` rewritten to match the new contract; `Settings.embedding_model` default flipped to `BAAI/bge-m3` (was the e5 768-dim placeholder).

## [0.1.0] - TBD

Initial release.
