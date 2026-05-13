# INFRA-001 — CI boot cold-cache HF Hub : timeouts + cache `~/.cache/huggingface/hub`

## Contexte

Les workers chargent `BAAI/bge-m3` (~2 GiB) au boot via SentenceTransformers. Sur un runner GitHub Actions sans cache HF, le download dépasse la fenêtre actuelle de 60s d'attente `/healthz` du job `contract` (`ci.yml`), causant des échecs CI intermittents. Le problème bloque l'avancée de EVAL-001 dont les jobs déjà préparés dans `.tmp-pr/eval.yml` reposent sur un boot workers fiable. Ce ticket aligne les fenêtres d'attente, étend le `start_period` healthcheck Docker pour les usages locaux, et introduit un cache GitHub Actions sur le répertoire HF Hub pour ramener un boot warm-cache sous 60s.

## Critères d'acceptation

- AC-1 : Dans `.github/workflows/ci.yml`, la boucle d'attente `/healthz` du job `contract` (start workers) attend jusqu'à 300 itérations de `sleep 1` (≥ 300s wall-time) avant de dumper `/tmp/uvicorn.log` et `exit 1`. Le message d'échec affiche `workers did not become healthy within 300 s`.
- AC-2 : Le job `contract` (`.github/workflows/ci.yml`) restaure et sauvegarde `~/.cache/huggingface/hub` via une étape `actions/cache@v4` placée **avant** `start workers`. La clé de cache est exactement `${{ runner.os }}-hf-hub-${{ hashFiles('workers/uv.lock') }}` (pas de fragment nom de modèle — `uv.lock` discrimine déjà via la version `sentence-transformers`). La `restore-keys` permet le fallback au préfixe `${{ runner.os }}-hf-hub-`. L'échec de l'étape `restore` (cache service indisponible, blob corrompu, miss inattendu) ne fait PAS échouer le job : on retombe sur le chemin cold-cache couvert par AC-4.
- AC-3 : Sur un run CI où la clé `actions/cache` HF Hub touche (cache hit), le boot workers atteint `200 OK` sur `/healthz` en ≤ 60s wall-time mesurés depuis le lancement de `uvicorn` jusqu'à la première réponse `2xx`. Le run émet la ligne `workers up after <N>s` avec `N <= 60`.
- AC-4 : Sur un run CI où la clé `actions/cache` HF Hub manque (cache miss, premier run ou invalidation `uv.lock`), le boot workers atteint `/healthz` 2xx en ≤ 300s wall-time. Le run émet `workers up after <N>s` avec `N <= 300`.
- AC-5 : Dans `docker-compose.yml`, le service `workers` déclare `healthcheck.start_period: 90s` (en plus des champs existants `test`, `interval`, `timeout`, `retries`). Aucun autre service (`postgres`, `redis`, `gateway`, `gcs`, `migrator`) n'est modifié par ce ticket.
- AC-6 : `docker compose up -d` sur poste de dev avec cache HF chaud (~/.cache/huggingface/hub déjà peuplé monté ou rebuild d'image sans purge) ne marque pas le service `workers` `unhealthy` pendant les 90s suivant son démarrage : `docker inspect --format '{{.State.Health.Status}}' archiviste-nocilia-workers-1` reste `starting` puis transitionne vers `healthy` sans passer par `unhealthy`.
- AC-7 : Exigence prescriptive pour tout workflow GitHub Actions du repo qui spawn les workers via `uvicorn` et attend `/healthz` : il DOIT appliquer la boucle d'attente 300s (cf AC-1) ET la step `actions/cache@v4` HF Hub avec la clé `${{ runner.os }}-hf-hub-${{ hashFiles('workers/uv.lock') }}` placée avant le start workers. INFRA-001 livre cette exigence pour `ci.yml` job `contract` uniquement ; `.tmp-pr/eval.yml` n'est PAS modifié dans ce ticket — EVAL-001 doit reprendre les deux briques au moment de sa promotion en `.github/workflows/eval.yml`. Toute future addition d'un workflow spawnant workers via `uvicorn` doit reprendre ce pattern.
- AC-8 : La PR INFRA-001 documente dans son message de commit (scope `chore(ci)` ou `chore(infra)`) la justification du seuil 300s (estimation ~2 GiB download HF Hub + chargement modèle, marge ×~2 vs mesure empirique typique d'un cold boot < 150s), sans exiger de benchmark formel checked-in.

## Non-goals

- Pas de modification des **branch protection settings** GitHub (required checks list). Le constat « les checks bloquent EVAL-001 » est documentaire ; l'ajustement éventuel de la required-checks list est un acte humain hors-ticket.
- Pas de pré-pull de `BAAI/bge-m3` à l'étape `setup-python` (pas de step `huggingface-cli download` séparé) — on s'appuie sur le boot uvicorn naturel + cache.
- Pas de changement du modèle d'embedding (`BAAI/bge-m3` reste le seul supporté phase 1).
- Pas d'ajout de service `huggingface-mirror` ou de proxy interne.
- Pas de mise en place d'une image Docker workers pré-bakée avec les poids `bge-m3` (option ADR future si le CI souffre encore après ce ticket).
- Pas de parallélisation des jobs CI ni de re-découpe du pipeline.
- Pas de chiffrement / signature du cache HF Hub (les poids sont publics).
- Pas de purge automatique du cache HF Hub côté GitHub Actions (laissée à la GC native d'`actions/cache`).
- Pas de retry de la boucle `/healthz` après timeout ; un seul tour de 300s.
- Pas de retouche du `Dockerfile` workers (pas de pré-bake) ni du `Dockerfile` gateway.
- Pas de modification du healthcheck `gateway` (start_period inchangé).

## Pre-conditions

- `BAAI/bge-m3` reste chargé au boot par `archiviste_workers.main:app` (lifespan SentenceTransformers singleton, cf RET-001 / GEN-001).
- `actions/cache@v4` disponible sur les runners (standard GitHub-hosted).
- `workers/uv.lock` versionné — sert de discriminant de cache stable (changement de version `sentence-transformers` ⇒ nouvelle clé ⇒ cache miss volontaire).
- Le runner GitHub-hosted dispose d'au moins ~3 GiB de disque libre dans `~/.cache/huggingface/hub` (vrai par défaut sur `ubuntu-latest`).

## Failure modes

- Cache `actions/cache` indisponible (panne GitHub service, restore non-bloquant) → comportement par défaut d'`actions/cache@v4` : un échec de `restore` n'interrompt pas le job, un échec de `save` post-job émet un warning sans faire échouer le run. Le boot retombe sur le chemin cold-cache 300s. AC-4 reste satisfait. Pas besoin de `continue-on-error: true` explicite.
- Téléchargement HF Hub > 300s (incident réseau extrême) → la boucle dump `/tmp/uvicorn.log` et `exit 1` ; le job CI échoue de manière lisible (pas de silent hang). Pas de retry phase 1.
- Cache hit corrompu / partiel → SentenceTransformers re-télécharge les fichiers manquants ; boot peut dépasser 60s mais reste ≤ 300s (AC-4 couvre). Pas de mécanisme de bust automatique phase 1.
- `start_period: 90s` insuffisant en local sur machine très lente (premier `docker compose up` cold-cache + build image) → service marqué `unhealthy` après 90s + `interval × retries` cumulés ; l'humain peut relancer ou monter un volume `~/.cache/huggingface/hub` (out-of-scope ticket).
- `workers/uv.lock` modifié mais aucune dépendance HF-impactante n'a changé → cache miss inutile, boot 300s sur ce run unique ; coût acceptable.

## Touch points (informatif, non contraignant pour l'architect)

- `.github/workflows/ci.yml` — job `contract` : ajout step `actions/cache@v4` avant `start workers` ; remplacement boucle `for i in {1..60}; do sleep 1; done` par `for i in $(seq 1 300); do ... done` ; alignement message d'échec.
- `docker-compose.yml` — service `workers`, sous `healthcheck`, ajout `start_period: 90s`.
- `.tmp-pr/eval.yml` — **non modifié par INFRA-001**. Référence informative uniquement : EVAL-001 devra appliquer le pattern AC-7 (boucle 300s + `actions/cache@v4` HF Hub) au moment de la promotion en `.github/workflows/eval.yml`.
- Pas de modification de code Python / Rust.
- Pas de modification de `migrations/`.
- `CHANGELOG.md` — entrée sous `## [Unreleased]` (Infra / CI).
- Éventuellement `docs/runbook.md` — note sur le cache HF Hub local (out-of-scope si pas demandé).

## Test oracle

- AC-1 : revue manuelle (`grep -n 'seq 1 300' .github/workflows/ci.yml`) + revue diff PR — assert présence boucle 300 itérations et message exact `workers did not become healthy within 300 s`.
- AC-2 : revue manuelle — assert présence step `uses: actions/cache@v4` avec `path: ~/.cache/huggingface/hub`, `key: ${{ runner.os }}-hf-hub-${{ hashFiles('workers/uv.lock') }}`, `restore-keys: ${{ runner.os }}-hf-hub-` ; step placée avant `start workers` ; pas de `continue-on-error` requis (comportement tolérant natif d'`actions/cache@v4`).
- AC-3 : intégration CI · observer le run CI une fois la PR INFRA-001 mergée (ou en re-run du job `contract`) avec cache populé → log `workers up after <N>s` avec `N <= 60`. Capture du log de step en artefact ou simple lecture humaine de l'output.
- AC-4 : intégration CI · run initial post-merge OU bump volontaire de `workers/uv.lock` → cache miss → `workers up after <N>s` avec `60 < N <= 300`. Vérifié en lecture humaine.
- AC-5 : contract · `grep -A 6 'workers:' docker-compose.yml | grep 'start_period: 90s'` → match ; `yamllint`/`actionlint` clean.
- AC-6 : intégration locale manuelle · `docker compose build workers && docker compose up -d workers postgres redis` sur poste auteur, puis `watch docker inspect ...` pendant 120s → état observé `starting` puis `healthy`, jamais `unhealthy`. Procédure documentée dans la description PR (pas de test automatisé).
- AC-7 : revue manuelle au `/plan` INFRA-001 — vérifier que seul `ci.yml` job `contract` est modifié dans cette PR et qu'aucune autre entrée de `.github/workflows/` spawnant workers n'a été oubliée. La conformité de `.tmp-pr/eval.yml` est explicitement reportée à EVAL-001 (hors-scope ici).
- AC-8 : revue manuelle du commit message — présence de la justification du seuil 300s.

## Performance / SLO

- Cible warm-cache : `/healthz` 2xx en ≤ 60s wall-time (AC-3).
- Cible cold-cache : `/healthz` 2xx en ≤ 300s wall-time (AC-4).
- Pas de mesure formelle / benchmark checked-in ; observation empirique sur les premiers runs post-merge.

## Security / trust boundary

- Pas de surface réseau nouvelle. `actions/cache` GitHub utilise des blobs scoping-PR/branch standard (cf docs GitHub).
- Pas de secret ni token additionnel.
- Le cache HF Hub contient des poids publics signés par Hugging Face ; pas d'élévation de privilège possible via empoisonnement de cache cross-PR (scope GitHub Actions native).
- Cohérence `.claude/rules/secret-hygiene.md` : aucun ajout de secret dans les workflows.

## Observability

- Logs CI existants suffisent : ligne `workers up after <N>s` (succès) ou bloc dump `--- uvicorn log ---` (échec). Pas de métrique nouvelle.
- Pas de log applicatif ajouté côté workers.

## Effort estimate

S — surface attendue : ~20 lignes diff cumulé sur `.github/workflows/ci.yml` + `docker-compose.yml` + entrée CHANGELOG. Pas de code, pas de migration, pas de test automatisé nouveau. `.tmp-pr/eval.yml` hors-scope.

## Status

ready
