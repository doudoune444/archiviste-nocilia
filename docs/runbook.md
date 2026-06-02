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

# 4. Premier boot : appliquer les migrations (obligatoire).
# `docker compose up -d` ne monte plus `./migrations` sur
# `/docker-entrypoint-initdb.d` — la base est vierge tant que `make migrate`
# n'a pas tourné. Sans cette étape : pas d'extensions `vector` / `pgcrypto`,
# pas de table `schema_version`, et toute requête applicative échouera.
make migrate
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
- **Les fichiers de migration NE DOIVENT PAS contenir `BEGIN` / `COMMIT` / `ROLLBACK`.** Le runner applique chaque fichier via `psql --single-transaction -f <file> -c "INSERT INTO schema_version ..."` : un `BEGIN`/`COMMIT` interne fermerait la transaction prématurément et l'`INSERT` s'exécuterait hors-tx, cassant la garantie AC-8. À traiter au prochain ticket migrations : ajouter une vérification statique dans `migrations/run.sh`.

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

## Cloud SQL schema bootstrap & migrations (OPS-003)

### One-time superuser bootstrap (manual, run once per DB instance)

This step is **not** in any migration file — it requires `cloudsqlsuperuser`
privileges. Run once after `terraform apply` creates the Cloud SQL instance,
before the first `deploy.yml` run.

```bash
# 1. Reset the postgres user password so you can connect interactively.
gcloud sql users set-password postgres \
  --instance=archiviste-db \
  --password=<strong-random-password>

# 2. Open a psql session via cloud-sql-proxy.
./cloud-sql-proxy --port 5432 <PROJECT_ID>:europe-west9:archiviste-db &
psql "postgresql://postgres:<password>@127.0.0.1:5432/archiviste"

# 3. Grant the runtime SA schema usage + create, and install extensions.
GRANT USAGE, CREATE ON SCHEMA public TO "archiviste-runtime@<project>.iam";
CREATE EXTENSION IF NOT EXISTS pgcrypto;
CREATE EXTENSION IF NOT EXISTS vector;
\q
```

These grants let the runtime SA (which owns all migration SQL) create tables
and types. Extensions require superuser and are intentionally one-shot.

### Ongoing migrations — automated in deploy.yml

After both canary revisions deploy, `deploy.yml` step `migrate` runs
`migrations/run.sh` via `cloud-sql-proxy v2 --auto-iam-authn
--impersonate-service-account=archiviste-runtime@<project>.iam.gserviceaccount.com`.
The runner is idempotent: rows already in `schema_version` are skipped.
Migration failure exits non-zero → rollback step fires, promote never runs.

### Running migrations manually via cloud-sql-proxy

```bash
# Download cloud-sql-proxy v2 (Linux).
curl -fsSL -o cloud-sql-proxy \
  https://storage.googleapis.com/cloud-sql-connectors/cloud-sql-proxy/v2.14.0/cloud-sql-proxy.linux.amd64
chmod +x cloud-sql-proxy

# Start proxy — impersonate runtime SA (requires tokenCreator grant, iam.tf).
./cloud-sql-proxy --auto-iam-authn \
  --impersonate-service-account=archiviste-runtime@<project>.iam.gserviceaccount.com \
  --port 5432 \
  <project>:europe-west9:archiviste-db &

# Wait for proxy to be ready.
for i in $(seq 1 30); do pg_isready -h 127.0.0.1 -p 5432 && break; sleep 1; done

# Run migrations (username = SA email with .gserviceaccount.com stripped, @ → %40).
export DATABASE_URL="postgresql://archiviste-runtime%40<project>.iam@127.0.0.1:5432/archiviste?sslmode=disable"
MIGRATIONS_DIR=./migrations bash migrations/run.sh
```

## §8 — Cloud SQL IAM authentication (SEC-005)

SEC-005 remplace l'hypothèse INFRA-002 PR-f « le proxy Cloud Run v2 injecte le
token IAM dans le slot password » (hypothèse fausse — voir `docs/blockers.md`
2026-05-29 §INFRA-002 PR-f). Les pools sqlx (gateway) et asyncpg (workers)
fetchent eux-mêmes un token IAM frais (`sqlservice.admin`) depuis le metadata
server et l'injectent dans chaque nouvelle connexion physique.

### Mécanisme (gateway / sqlx)

1. Au boot, `run()` fetch un premier token (`GET metadata.google.internal/…/token?scopes=sqlservice.admin`). Échec → exit non-zéro + log `event=boot.sql_pool_init_failed reason_code=metadata_token_failed`.
2. `PgPoolOptions.connect_with(PgConnectOptions.password(token))` établit le pool.
3. Un background task (`tokio::spawn`) rafraîchit le token toutes les 30 min via `pool.set_connect_options(new_opts)`.
4. `max_lifetime = 45 min` garantit que toute connexion physique est recyclée avant l'expiration du token (TTL IAM = 60 min).
5. `before_acquire` gate ferme les connexions proches de l'expiration (défense en profondeur).

### Vérification post-déploiement

```bash
# Steady state : aucun event sql_pool.connection_failed ne doit apparaître.
gcloud run services logs read archiviste-gateway --limit=100 \
  | grep sql_pool.connection_failed
# Attendu : 0 lignes.

gcloud run services logs read archiviste-workers --limit=100 \
  | grep sql_pool.connection_failed
# Attendu : 0 lignes.

# Vérifier les bindings Cloud SQL (INFRA-002 PR-e) si boot échoue.
gcloud projects get-iam-policy <PROJECT> \
  --flatten="bindings[].members" \
  --filter="bindings.role:roles/cloudsql.instanceUser" \
  --format="table(bindings.members)"
```

### Local dev

Connexion locale via docker-compose Postgres (password=`postgres`) — pas de
metadata server disponible hors Cloud Run. Le mécanisme IAM n'est pas exercé
en dev local : `DATABASE_URL` utilise le mot de passe littéral `postgres`.

Pour tester le chemin IAM en local (optionnel) :

```bash
# 1. ADC — identité GCP locale
gcloud auth application-default login

# 2. Binding roles/cloudsql.instanceUser (déjà fait INFRA-002 PR-e pour le SA runtime).
#    Pour un dev qui veut se connecter directement :
gcloud projects add-iam-policy-binding <PROJECT> \
  --role=roles/cloudsql.instanceUser \
  --member=user:<dev-email>
```

Les bindings `roles/cloudsql.client` et `roles/cloudsql.instanceUser` sur le SA
`archiviste-runtime` ont été provisionnés par INFRA-002 PR-e. Voir §5 pour le
setup GCS signBlob (même SA, scope distinct).

## §5 — GCS V4 signing via IAM signBlob (SEC-004)

La gateway signe les URLs GCS via `iamcredentials.googleapis.com/v1/.../signBlob`
en utilisant le token OAuth du metadata server Cloud Run — pas de clé SA privée.

### Vérification post-déploiement

```bash
# Vérifier que le signing fonctionne depuis le container déployé.
curl -sH "Authorization: Bearer <author-jwt>" \
  https://<gateway-host>/v1/conversations/<test-conv-id>/signed-url
# Attendu : 200 + {"signed_url":"https://archiviste-conversations.storage.googleapis.com/...","expires_at":"..."}
# Si 503 : inspecter les logs Cloud Run pour event=dashboard.signing_failed + reason_code.
```

### Local dev

Endpoint `/v1/conversations/{id}/signed-url` retourne 503 en local dev sans configuration
IAM — c'est le comportement attendu (pas de fallback, `secret-hygiene.md`).
Activation locale (rarement exercée — UI-002 dev typiquement contre données stagées) :

```bash
gcloud auth application-default login
gcloud iam service-accounts add-iam-policy-binding \
  archiviste-runtime@<project>.iam.gserviceaccount.com \
  --role=roles/iam.serviceAccountTokenCreator \
  --member=user:<dev-email>
```

## Post-deploy smoke check — workers IAM ingress (SEC-006)

After any `terraform apply` that touches `google_cloud_run_v2_service.workers`
or its IAM bindings, run these two checks in order.

### Pre-deploy gate (Terraform check block — AC-10)

`terraform plan` enforces that `google_cloud_run_v2_service_iam_member.workers_runtime_invoker`
is never `allUsers` nor `allAuthenticatedUsers`. A plan that violates this exits
non-zero before `apply` runs. See `infra/terraform/checks.tf`.

### Post-deploy gate — unauthenticated request MUST return 403

```bash
curl -sw '%{http_code}\n' -o /dev/null https://<workers-url>/health
```

Expected output: `403`

Any other response code is an incident:

- `200` — critical IAM regression: workers is publicly accessible without
  authentication. Immediately inspect `terraform state` and re-verify the
  `ingress` setting and the `workers_runtime_invoker` IAM binding.
- `404` / `502` — workers is unreachable or misconfigured (not a security
  breach, but still requires investigation before declaring the deploy healthy).

### Post-deploy gate — authenticated gateway→workers path MUST return 200

```bash
curl -H "Authorization: Bearer <user-jwt>" \
     -X POST https://<gateway-url>/v1/chat \
     -d '{"query":"ping","conversation_id":"<uuid>"}'
```

Expected: `200`. A `503` here typically means the gateway ID-token fetch failed
(check Cloud Run logs for `event=chat.id_token_failed`) or workers is still
booting.

### Cross-reference

- Pre-deploy: `infra/terraform/checks.tf` `check "workers_iam_no_public_invoker"` (AC-10).
- Post-deploy: curl `403` above (AC-11).

## Ingestion lore (ING-001)

Pipeline CLI one-shot : parcourt `lore/`, parse frontmatter YAML, normalise
NFKC + strip controls, chunk via `RecursiveCharacterTextSplitter` (tokenizer
`BAAI/bge-m3`, 512/64), embed avec sentence-transformers, UPSERT idempotent
dans `documents` + `chunks` (transaction par fichier, hash SHA-256 du corps
normalisé).

## Sync Google Drive (ING-013 + ING-011)

Synchronise un dossier Google Drive vers `lore/` via l'API Drive v3. Supporte
gdoc → `.md` (export Markdown), PNG natif → `.png`, Google Sheets → `.md`
(un fichier par onglet, table GFM), Google Slides → `.md` (un fichier par
présentation, `## Slide N`). Détection créé/updated/renamed/archived/unchanged,
state persisté dans `scripts/.gdrive_state.json`.

### Prérequis

1. **Service account GCP** avec scopes additionnels Sheets et Slides (ING-011) :
   ```bash
   gcloud iam service-accounts create gdrive-sync-sa \
     --display-name "GDrive Sync SA"
   # Télécharger la clé JSON
   gcloud iam service-accounts keys create gdrive-sa-key.json \
     --iam-account gdrive-sync-sa@<PROJECT>.iam.gserviceaccount.com
   ```

   Le script déclare les scopes suivants dans les credentials SA :
   - `https://www.googleapis.com/auth/drive.readonly`
   - `https://www.googleapis.com/auth/spreadsheets.readonly` (ING-011)
   - `https://www.googleapis.com/auth/presentations.readonly` (ING-011)
   - `https://www.googleapis.com/auth/documents.readonly` (ING-014)

   **ING-014** : l'API Google Docs doit également être activée sur le projet GCP :
   ```bash
   gcloud services enable docs.googleapis.com --project <PROJECT>
   ```
   Si ce scope ou cette API manque, le script sort en erreur au premier
   `documents.get` avec le message `gdrive docs scope missing: enable
   https://www.googleapis.com/auth/documents.readonly on the service account`.

   Le partage de fichiers GSheet/GSlide avec le service account (via Drive)
   suffit pour obtenir l'accès en lecture ; aucune configuration IAM
   supplémentaire n'est requise. Si les scopes manquent, le script sort en
   erreur au démarrage avec le message `gdrive scope missing: spreadsheets.readonly
   and presentations.readonly required`.

2. **Partager le dossier Drive** avec l'email du service account
   (`gdrive-sync-sa@<PROJECT>.iam.gserviceaccount.com`) en lecture seule.

3. **Variables d'environnement** (copier `.env.example` → `.env`, remplir) :
   ```bash
   # Option A — JSON inline
   export GDRIVE_SA_KEY_JSON=$(cat gdrive-sa-key.json)
   # Option B — chemin vers fichier
   export GOOGLE_APPLICATION_CREDENTIALS=/path/to/gdrive-sa-key.json
   # Identifiant du dossier racine Drive (visible dans l'URL du dossier)
   export GDRIVE_ROOT_FOLDER_ID=<FOLDER_ID>
   ```

### Invocation manuelle

```bash
cd scripts
# Sync réel (écrit dans lore/, met à jour scripts/.gdrive_state.json)
uv run python -m gdrive_export --root-folder-id $GDRIVE_ROOT_FOLDER_ID

# Dry-run : affiche les actions sans rien écrire (exit code toujours 0)
uv run python -m gdrive_export --root-folder-id $GDRIVE_ROOT_FOLDER_ID --dry-run
```

Logs JSON sur stdout : `gdrive_sync.start`, un `gdrive_sync.file` par fichier,
`gdrive_sync.summary` en fin. Exit code 0 si zéro erreur, 1 sinon.

### State

`scripts/.gdrive_state.json` est versionné en git. Chaque run le met à jour.
La différence inter-runs est visible via `git diff scripts/.gdrive_state.json`.
Le backup `.bak` (écriture atomique) est ignoré par `.gitignore`.

### Ingestion DB après sync

Après un sync Drive, déclencher l'ingesteur ING-001 pour indexer les nouveaux
`.md` dans pgvector :

```bash
cd workers
uv run python -m archiviste_workers.ingest --path ../lore/
```

Pré-requis :
- FOUND-002 : `make migrate` appliqué (extensions + `schema_version`).
- FOUND-003 : `documents` + `chunks` créés.
- Premier run : réseau sortant pour télécharger `BAAI/bge-m3` (~2.3 GiB) dans
  `~/.cache/huggingface/`. Runs suivants : cache hit, aucune requête sortante.

Variables d'environnement :
- `DATABASE_URL` (cf `.env.example`).
- `EMBED_BATCH_SIZE` (optionnel, défaut `32`).

Commande :

```bash
cd workers
uv run python -m archiviste_workers.ingest --path lore/
```

Comportement :
- `inserted` : nouveau `source_path`, INSERT documents + chunks.
- `skipped` (`reason: unchanged`) : `content_hash` identique → aucune écriture.
- `updated` : hash différent → `DELETE chunks` + `UPDATE documents` + nouvelle
  séquence chunks dans une transaction unique.
- `error` : frontmatter invalide, fichier > 1 MiB, ou erreur DB.

Logs JSON sur stdout : `ingest.start`, un `ingest.document` par fichier,
`ingest.summary` final. Exit code 0 si zéro erreur, 1 sinon (2 = init fatal).

## Onboarding gdrive-sync

Procédure complète pour activer le workflow `gdrive-sync.yml` sur un nouveau
repo (ou après rotation de credentials).

### (a) Créer le Service Account GCP et générer la clé

Voir la section `## Sync Google Drive` ci-dessus pour la commande `gcloud iam
service-accounts create` et le téléchargement de la clé JSON. Scopes requis :

- `https://www.googleapis.com/auth/drive.readonly` (obligatoire)
- `https://www.googleapis.com/auth/spreadsheets.readonly` (si ING-011 mergé)
- `https://www.googleapis.com/auth/presentations.readonly` (si ING-011 mergé)
- `https://www.googleapis.com/auth/documents.readonly` (si ING-014 mergé)

Activer les APIs GCP nécessaires :
```bash
gcloud services enable drive.googleapis.com --project <PROJECT>
gcloud services enable sheets.googleapis.com --project <PROJECT>
gcloud services enable slides.googleapis.com --project <PROJECT>
gcloud services enable docs.googleapis.com --project <PROJECT>
```

### (b) Partager le dossier Drive racine en lecture

Partager le dossier Drive racine avec l'email du service account
(`gdrive-sync-sa@<PROJECT>.iam.gserviceaccount.com`) en tant que « Lecteur ».
Aucune configuration IAM GCP supplémentaire n'est nécessaire.

### (c) Ajouter le secret `GDRIVE_SA_KEY` au repo

```bash
# Contenu de la clé JSON SA inline (single line)
gh secret set GDRIVE_SA_KEY \
  --repo <OWNER>/<REPO> \
  --body "$(cat gdrive-sa-key.json)"
```

Le secret est injecté en env step uniquement (`GDRIVE_SA_KEY_JSON`) ; il
n'est jamais persisté sur le filesystem du runner ni loggué.

### (d) Ajouter la variable `GDRIVE_ROOT_FOLDER_ID`

`GDRIVE_ROOT_FOLDER_ID` est une variable de repo (non-secret) : c'est l'ID du
dossier Drive racine (visible dans l'URL, ex. `1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs`).

```bash
gh variable set GDRIVE_ROOT_FOLDER_ID \
  --repo <OWNER>/<REPO> \
  --body "<FOLDER_ID>"
```

### (e) Déclencher le premier run et reviewer la PR auto

```bash
# Déclenchement manuel via gh CLI
gh workflow run gdrive-sync.yml --repo <OWNER>/<REPO>

# Suivre l'exécution
gh run list --workflow gdrive-sync.yml --repo <OWNER>/<REPO> --limit 3
```

Si des fichiers Drive ont été trouvés, une PR `chore/gdrive-sync-<run_id>`
est ouverte automatiquement vers `main` avec le summary du run en body.
Reviewer le diff, puis merger manuellement. Déclencher ensuite l'ingesteur
ING-001 :

```bash
cd workers
uv run python -m archiviste_workers.ingest --path ../lore/
```

Si `secrets.GDRIVE_SA_KEY` est absent, le workflow échoue en première step
avec le message `secret GDRIVE_SA_KEY missing — see docs/runbook.md` sans
effectuer de checkout ni d'installation.
