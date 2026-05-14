# Plan — INFRA-001 CI cold-cache HF Hub: 300s wait + actions/cache + start_period 90s

## Goal
Aligner la fenêtre d'attente `/healthz` à 300s sur tout job GHA qui spawn workers via uvicorn (`ci.yml` job `contract`, `eval.yml` job `ragas`), ajouter un cache GHA sur `~/.cache/huggingface/hub` clé `uv.lock`, et étendre `start_period` workers à 90s pour rendre le boot CI fiable cold-cache (≤300s) et warm-cache (≤60s).

## Acceptance criteria recap
- AC-1: Boucle `/healthz` job `contract` = 300 itérations `sleep 1`, message d'échec `workers did not become healthy within 300 s`, dump `/tmp/uvicorn.log` + `exit 1`.
- AC-2: Step `actions/cache@v4` avant `start workers`, `path: ~/.cache/huggingface/hub`, `key: ${{ runner.os }}-hf-hub-${{ hashFiles('workers/uv.lock') }}`, `restore-keys: ${{ runner.os }}-hf-hub-`. Pas de `continue-on-error` (tolérance native).
- AC-3: Cache hit → `workers up after <N>s` avec `N <= 60`.
- AC-4: Cache miss → `workers up after <N>s` avec `N <= 300`.
- AC-5: `docker-compose.yml` service `workers` healthcheck.`start_period: 90s` ajouté ; aucun autre service modifié.
- AC-6: `docker compose up -d` warm-cache local → status `starting` → `healthy`, jamais `unhealthy` dans les 90s.
- AC-7: Pattern (boucle 300s + actions/cache HF Hub) appliqué à tout workflow GHA qui spawn workers via uvicorn → `ci.yml` job `contract` ET `eval.yml` job `ragas`. `.tmp-pr/eval.yml` non modifié. `boot-sla.yml` hors-champ (docker compose, pas uvicorn direct).
- AC-8: Commit message scope `chore(ci)` ou `chore(infra)` justifie le seuil 300s (~2 GiB download HF + chargement modèle, marge ×~2 vs cold boot empirique <150s).

## Files to touch
- `.github/workflows/ci.yml` — job `contract` : (a) ajout step `actions/cache@v4` HF Hub avant `start workers` ; (b) remplacement `for i in {1..60}` → `for i in $(seq 1 300)` ; (c) message d'échec → `workers did not become healthy within 300 s`.
- `.github/workflows/eval.yml` — job `ragas` : (a) ajout step `actions/cache@v4` HF Hub (`path: ~/.cache/huggingface/hub`, `key: ${{ runner.os }}-hf-hub-${{ hashFiles('workers/uv.lock') }}`, `restore-keys: ${{ runner.os }}-hf-hub-`) avant `start workers` ; (b) remplacement boucle `for _ in {1..30}; do curl -sf http://localhost:8000/healthz && break; sleep 1; done` → `for i in $(seq 1 300); do if curl -sf http://localhost:8000/healthz; then echo "workers up after ${i}s"; exit 0; fi; sleep 1; done; echo "workers did not become healthy within 300 s"; exit 1` ; (c) aligner aussi capture log uvicorn (redirect `> /tmp/uvicorn.log 2>&1 &` + dump à l'échec) pour parité diagnostic avec `ci.yml`.
- `docker-compose.yml` — service `workers` healthcheck : ajout `start_period: 90s` (en plus de `test`/`interval`/`timeout`/`retries`).
- `CHANGELOG.md` — entrée sous `## [Unreleased]` section CI/Infra.

## Test strategy
- AC-1, AC-2, AC-5, AC-7 : revue manuelle diff + `grep` (cf Test oracle spec L57-66) sur `ci.yml` ET `eval.yml`.
- AC-3, AC-4 : observation empirique runs CI post-merge (warm/cold) sur jobs `contract` + `ragas`. Pas de test automatisé checked-in (cf SLO L72).
- AC-6 : procédure manuelle locale documentée dans description PR (`docker compose build workers && up -d` + `docker inspect ... Health.Status`).
- AC-8 : revue commit message.
- Lint : `actionlint` (déjà wired job `lint`) sur les deux fichiers édités.
- Pas de test unitaire, pas de property test, pas de schemathesis (pas d'OpenAPI touché), pas de migration, pas de Ragas re-run forcé.

## Implementation steps (ordered)
1. Édition `.github/workflows/ci.yml` job `contract` : insérer step `actions/cache@v4` immédiatement avant `start workers` (après `provision pgvector extension`).
2. Édition même job : remplacer la boucle `for i in {1..60}; do sleep 1; done` par `for i in $(seq 1 300); do ... sleep 1; done` ; aligner message `workers did not become healthy within 300 s`.
3. Édition `.github/workflows/eval.yml` job `ragas` : insérer step `actions/cache@v4` HF Hub avant `start workers` (après `seed minimal corpus`).
4. Édition même job : remplacer la boucle 30s par la boucle 300s alignée sur `ci.yml` (echo `workers up after ${i}s` + dump `/tmp/uvicorn.log` + `exit 1` à l'échec).
5. Édition `docker-compose.yml` service `workers` : ajouter `start_period: 90s` sous `healthcheck:` (après `retries: 5`).
6. Ajout entrée `CHANGELOG.md` sous `## [Unreleased]` : `chore(ci): INFRA-001 align /healthz wait to 300 s + cache HF Hub (cold-cache fix)` + note `start_period: 90s` workers + mention `eval.yml` job `ragas` couvert.
7. CI gate local : `actionlint` sur `.github/workflows/ci.yml` + `.github/workflows/eval.yml` ; relire diff.
8. Commit message rédigé avec justification 300s (spec AC-8) ; présentation humain avant `git commit`.

## Risks / open questions
- **`actions/cache@v4` restore non-bloquant** : spec L41 affirme que le comportement natif (échec restore = warning, pas d'échec job) suffit, donc pas de `continue-on-error`. Vérifier docs `actions/cache@v4` à l'impl ; si comportement a changé (v4 strictifie), ajouter `continue-on-error: true` explicite et signaler en commit.
- **`hashFiles('workers/uv.lock')` chemin relatif** : `actions/cache` résout depuis `$GITHUB_WORKSPACE`. Ni `contract` (ci.yml) ni `ragas` (eval.yml) n'ont de `defaults.run.working-directory` au niveau job, donc `workers/uv.lock` est correct depuis la racine. À ne PAS écrire `uv.lock` sans préfixe.
- **Position step cache** : doit être après `actions/checkout@v4` (sinon pas de `uv.lock` à hasher) et avant `start workers`. Choix retenu = **juste avant `start workers`**, conforme littéralement à AC-2. Décidable en impl sans nouvelle question humaine.
- **`start_period: 90s` cold-cache local** : si premier `up` cold-cache local dépasse 90s, service marqué `unhealthy` (cf Failure modes L44). Accepté par spec — humain peut monter volume `~/.cache/huggingface/hub` (out-of-scope).

## Out of scope
- Pas de modif `.tmp-pr/eval.yml` (artefact `.tmp-pr/` non-tracké, hors workflows live).
- Pas de modif `.github/workflows/boot-sla.yml` (docker compose path, pas uvicorn direct).
- Pas de modif `.github/workflows/{release-please,gdrive-sync}.yml` (ne spawn pas workers).
- Pas de pré-pull `huggingface-cli download` séparé (Non-goal spec L21).
- Pas de modif `Dockerfile` workers ni gateway.
- Pas de modif healthcheck `gateway`, `postgres`, `redis`, `gcs`, `migrator`.
- Pas de changement modèle d'embedding.
- Pas de retry boucle `/healthz` après timeout (un seul tour 300s).
- Pas de purge automatique cache HF Hub (GC native `actions/cache`).
- Pas de mise à jour branch protection / required checks list (acte humain hors-ticket).
- Pas de code Python / Rust modifié, pas de migration, pas de spec / openapi / properties touchés.
- Pas d'entrée `docs/runbook.md` (spec L55 « out-of-scope si pas demandé »).
