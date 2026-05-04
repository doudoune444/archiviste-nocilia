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

Le runner est un conteneur jetable lancé via `make migrate`
(`docker compose --profile tools run --rm migrator`). Il lit chaque fichier
`migrations/NNNN_<slug>.sql`, applique en transaction (`BEGIN ... COMMIT`)
ceux absents de la table `schema_version`, puis insère la ligne `(version,
description, applied_at)` correspondante. Voir `migrations/run.sh`.

```bash
# Nouvelle migration : créer le fichier (4 chiffres, snake_case, .sql)
$EDITOR migrations/0002_my_change.sql

# Appliquer (lit DATABASE_URL depuis .env)
make migrate
```

Conventions et garanties :

- Nommage `^[0-9]{4}_[a-z0-9_]+\.sql$` strict ; un fichier hors-norme fait sortir le runner en erreur.
- Une migration = une transaction. Si la version `N` échoue, seule `N` rollback ; les versions `<N` déjà committées restent appliquées.
- Une version `N` déjà présente est sautée (log `migration N already applied, skipping`).
- Gap detection : si un fichier `N` est absent de `schema_version` alors qu'une version supérieure y figure, le runner sort en erreur (`migration gap: file version N missing ...`) sans rien appliquer.
- Pas de down/rollback automatisé (out of scope FOUND-002). Procédure manuelle ad hoc via `psql $DATABASE_URL`.

Tests d'intégration du runner : `bash tests/migrations/run_tests.sh` (Docker requis).

## SLA boot local

Le script `scripts/measure-boot.sh` vérifie via `docker image inspect` la
présence des 4 images (`postgres`, `redis`, `workers`, `gateway`) avant
`docker compose up -d`, puis mesure `total_seconds` et le `healthy_at_seconds`
de chaque service via le polling de `docker compose ps --format json`. Il
écrit un artefact JSON conforme au schéma de l'annexe AC-12.

Baselines de référence pour la calibration :

- **Dev local** : 4 cœurs / 8 GiB RAM / SSD. Cible SLA `total_seconds <= 30s`.
- **CI** : `ubuntu-latest` GitHub Actions runner. Variance acceptée vs baseline locale ; mesure non-bloquante (`continue-on-error: true` dans `.github/workflows/boot-sla.yml`).

Le script sort toujours en code 0 (sauf images manquantes, qui font sortir
en non-zéro avec `Image <name> missing. Run 'docker compose build' first.`).
Le booléen `passed` dans l'artefact reflète la comparaison `total_seconds <= sla_seconds`.

Lancement local :

```bash
docker compose build
make boot-measure  # ou: bash scripts/measure-boot.sh
cat boot-metrics.json
```

Note Windows : `make`, `bash` et les scripts POSIX supposent Git Bash, WSL,
ou un shell équivalent.
