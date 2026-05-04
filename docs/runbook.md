# Runbook — Archiviste Nocilia

## Démarrage local

```bash
# 1. Pré-requis
# - Rust 1.95+ (rustup)
# - Python 3.12+ + uv
# - Docker + Docker Compose
# - gh CLI authentifié

# 2. Clone et installe deps
cd archiviste-nocilia
cd gateway && cargo build && cd ..
cd workers && uv sync && cd ..

# 3. Stack complet
cp .env.example .env
# Éditer .env avec les clés API
docker compose up -d
```

Vérification :

```bash
curl http://localhost:8080/healthz
# {"status":"ok","version":"0.1.0"}
```

## Tests

```bash
# Gateway
cd gateway && cargo test
cd gateway && cargo clippy -- -D warnings

# Workers
cd workers && uv run pytest
cd workers && uv run ruff check . && uv run mypy src/

# Contrat OpenAPI
docker compose up -d
uv run schemathesis run specs/openapi/gateway-to-workers.yml --base-url http://localhost:8080

# Eval RAG
uv run python eval/ragas_runner.py --set specs/golden_qa.jsonl
```

## Workflow de développement (workflow Claude Code)

```bash
# 1. Nouveau ticket — authoring guidé recommandé
/spec FOUND-002 "Ingestion pipeline minimal pour documents Markdown locaux"
# L'agent spec-author pose ≤ 5 questions, écrit le draft, itère.
# (Alternative : /ticket FOUND-002 "..." pour stub vide à remplir à la main.)
# Tu réponds aux open questions, ré-invoques /spec FOUND-002 pour itérer.
# Quand checklist green : tu dis "ready" → l'agent passe Status: ready.

# 2. Crée branche feature (toi, pas l'agent)
git checkout -b feat/FOUND-002-ingestion-pipeline

# 3. Plan
/plan FOUND-002
# Lis specs/plans/FOUND-002.md, valide ou itère

# 4. Implémentation
/impl FOUND-002

# 5. Review adversarial
/review FOUND-002

# 6. Eval (si RAG path touché)
/eval FOUND-002

# 7. Ship
/ship FOUND-002
# → ouvre PR
```

## Incidents fréquents

### Gateway healthcheck fails

```bash
docker compose logs gateway
# Vérifier env vars (DB_URL, WORKERS_URL)
# Vérifier que workers tourne : curl http://localhost:8000/healthz
```

### Workers `pgvector` extension manquante

```bash
docker compose exec postgres psql -U postgres -d archiviste -c "CREATE EXTENSION IF NOT EXISTS vector;"
```

### Eval scores chutent après changement

1. `/debug "eval scores dropped after commit <SHA>"` → identifier root cause
2. Ne **jamais** baisser `eval/baseline.json` sans approbation explicite

## Secrets

- Local : `.env` (gitignored)
- Prod : GCP Secret Manager, injectés via Cloud Run env vars

Jamais de secret en clair dans le repo, jamais de `.env` committé.

## Migrations DB

```bash
# Nouvelle migration
cd gateway
sqlx migrate add <name>
# Édite migrations/<timestamp>_<name>.sql

# Appliquer en local
sqlx migrate run

# Rollback (manuel, pas de down auto par défaut)
psql $DATABASE_URL -f migrations/down/<timestamp>_<name>.sql
```
