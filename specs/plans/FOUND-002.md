# Plan — FOUND-002 Stack locale reproductible & SLA boot

## Goal
Figer la stack locale (postgres + redis + workers + gateway), introduire un runner de migrations versionnées idempotent et transactionnel, et instrumenter une mesure non-bloquante du temps de boot (SLA 30 s) archivée en CI.

## Acceptance criteria recap
- AC-1 : `docker compose up -d` démarre `postgres`, `redis`, `workers`, `gateway`, tous `healthy` (visible via `docker compose ps --format json`).
- AC-2 : `redis` exige `REDIS_PASSWORD` ; `redis-cli -a $REDIS_PASSWORD PING` -> `PONG`, sans `-a` -> rejet.
- AC-3 : Volume nommé `redis-data` monté `/data`, `--appendonly yes` ; clé survit `docker compose restart redis`.
- AC-4 : `.env.example` versionné déclare `REDIS_PASSWORD` et `DATABASE_URL` ; `.env` dans `.gitignore`.
- AC-5 : Migrations nommées `^[0-9]{4}_[a-z0-9_]+\.sql$`, version extraite du nom.
- AC-6 : Service `migrator` sous `profiles: ["tools"]`, non démarré par `up -d`.
- AC-7 : `make migrate` lance `docker compose run --rm migrator` (image `postgres:16`, `./migrations:/migrations:ro`, `DATABASE_URL` via `.env`), applique fichiers absents de `schema_version` en ordre croissant et insère `(version, description, applied_at)`.
- AC-8 : Chaque migration en transaction dédiée (`BEGIN; ... ; INSERT schema_version ...; COMMIT;`). Échec sur `N` -> seule `N` rollback.
- AC-9 : Version déjà présente -> log `migration N already applied, skipping`.
- AC-10 : Gap (`MAX(version) > N` mais fichier `N` absent de `schema_version`) -> exit non-zéro, message `migration gap: file version N missing from schema_version while higher version applied`, base inchangée.
- AC-11 : `scripts/measure-boot.sh` valide via `docker image inspect` la présence des 4 images avant `up -d` ; image manquante -> exit non-zéro et message `Image <name> missing. Run 'docker compose build' first.`.
- AC-12 : Le script mesure `total_seconds`, par-service `healthy_at_seconds`, écrit JSON conforme à l'annexe (`total_seconds`, `sla_seconds`, `passed`, `services[].name`, `services[].healthy_at_seconds`).
- AC-13 : `passed = (total_seconds <= sla_seconds)` avec `sla_seconds=30` ; exit 0 quel que soit `passed`.
- AC-14 : `docs/runbook.md` documente baseline « Dev local : 4 cœurs / 8 GiB RAM / SSD » et « CI : ubuntu-latest GitHub Actions runner ».
- AC-15 : `.github/workflows/boot-sla.yml` (séparé de `ci.yml`) sur `pull_request` + `push` main : `docker compose build` puis `scripts/measure-boot.sh`, upload artefact JSON (rétention 30 j), `continue-on-error: true` sur l'étape mesure.

## Files to touch
- `docker-compose.yml` — ajout services `redis` (no host port, healthcheck auth) + `migrator` (profile tools), volume `redis-data`, propagation `REDIS_PASSWORD` (workers/gateway via env).
- `.env.example` — nouveau, clés `REDIS_PASSWORD=changeme` et `DATABASE_URL=postgres://postgres:postgres@postgres:5432/archiviste`.
- `.gitignore` — vérifier `.env` (déjà présent ligne 2) ; pas de modif si OK.
- `Makefile` — nouveau, cible `migrate` -> `docker compose --profile tools run --rm migrator`.
- `migrations/run.sh` — nouveau, runner bash : valide regex nom, lit `schema_version`, détecte gap, applique fichiers manquants en transaction, log structuré.
- `migrations/0001_init.sql` — RETIRER l'`INSERT INTO schema_version` (cf Risks). Humain-only : approbation explicite requise.
- `scripts/measure-boot.sh` — nouveau, vérifie images, lance `up -d`, polling `docker compose ps --format json` jusqu'à `healthy` pour les 4 services, écrit JSON.
- `scripts/check-ports.sh` — RAS (redis sans port hôte exposé, donc rien à ajouter).
- `.github/workflows/boot-sla.yml` — nouveau workflow dédié.
- `docs/runbook.md` — section « SLA boot » avec baselines + remplacer la section « Migrations DB » obsolète (mention `sqlx migrate` à supprimer car non implémentée) par la nouvelle procédure `make migrate`.
- `CHANGELOG.md` — entrée `## [Unreleased]`.
- `migrations/tests/` (ou `tests/migrations/`) — fixtures pour AC-8/AC-9/AC-10 (fichiers SQL bidons + script bash de test).

## Test strategy
- Integration boot (AC-1, AC-2, AC-3) : script bash dans `scripts/measure-boot.sh` lui-même + un test ad hoc `tests/integration/test_stack.sh` lancé en CI ; oracle = `docker compose ps --format json | jq` + scénario set/restart/get Redis.
- Contract (AC-4, AC-5, AC-6, AC-12, AC-14, AC-15) : checks bash purs (`grep`, `test -f`, `docker compose config`, validation JSON via `jq`/python `jsonschema`).
- Integration migrations (AC-7, AC-8, AC-9, AC-10) : suite bash `tests/migrations/run_tests.sh` qui démarre une postgres jetable, exerce 4 scénarios (vierge / ré-exécution / erreur SQL milieu / gap manuel).
- Property : aucune (aucun invariant `specs/properties.md` concerné).
- Contract OpenAPI : non touché.
- Eval : non concerné.

## Implementation steps (ordered)
1. `.env.example` + vérif `.gitignore` (AC-4).
2. `docker-compose.yml` : ajout `redis` (sans port hôte, command `--requirepass $REDIS_PASSWORD --appendonly yes`, volume `redis-data:/data`, healthcheck `redis-cli -a $$REDIS_PASSWORD PING`), volume `redis-data`. Tester `up -d` (AC-1, AC-2, AC-3).
3. Renommer `migrations/0001_init.sql` (humain-approve) : retirer l'INSERT, le runner s'en charge. Démonter le mount `/docker-entrypoint-initdb.d` du service postgres pour éviter double application (cf Risks).
4. `migrations/run.sh` : parsing regex, lecture `schema_version`, boucle transactionnelle, gap-check (AC-5, AC-7, AC-8, AC-9, AC-10).
5. Service `migrator` dans `docker-compose.yml` sous `profiles: ["tools"]`, image `postgres:16`, mount `./migrations:/migrations:ro`, entrypoint `/migrations/run.sh` (AC-6).
6. `Makefile` cible `migrate` (AC-7).
7. Tests migrations (`tests/migrations/run_tests.sh`).
8. `scripts/measure-boot.sh` : vérification images (`docker image inspect`), lancement `up -d`, polling healthy, génération JSON conforme annexe (AC-11, AC-12, AC-13).
9. `.github/workflows/boot-sla.yml` (AC-15).
10. `docs/runbook.md` : ajout baselines SLA + réécriture section « Migrations DB » (AC-14).
11. `CHANGELOG.md` entrée `[Unreleased]`.

## Risks / open questions
- **Conflit double-application schema_version** : `migrations/0001_init.sql` est aujourd'hui mounté sur `/docker-entrypoint-initdb.d` du service postgres ET fait `INSERT INTO schema_version (1, ...)`. Avec le nouveau runner qui veut faire l'INSERT, on aurait double-insert (PK conflict) ou skip silencieux. Résolution proposée : (a) retirer le mount `/docker-entrypoint-initdb.d` côté postgres dans `docker-compose.yml`, (b) retirer l'INSERT du fichier `0001_init.sql`. Les deux modifient des fichiers humain-only (`docker-compose.yml` est OK ; `migrations/*.sql` nécessite approbation explicite humaine). À valider avant impl.
- **Redis healthcheck avec password** : `redis-cli -a $$REDIS_PASSWORD PING` log un warning sur stderr ; à confirmer que healthcheck OK quand même (sinon `redis-cli --no-auth-warning -a ...`).
- **Variable `REDIS_PASSWORD` dans `.env.example`** : valeur placeholder `changeme` ; à confirmer pas de secret réel.
- **`continue-on-error` sur l'étape mesure (AC-15)** : le workflow reste vert même si script crash ; à confirmer que c'est l'intention (vs `continue-on-error` uniquement sur l'assertion SLA).
- **OS dev** : `Makefile` + scripts bash supposent shell POSIX. Sous Windows pur, l'utilisateur doit passer par WSL ou Git Bash. À documenter dans runbook ?
- **Gap detection (AC-10)** : sémantique de « `MAX(version) > N` en base mais fichier `N` absent de `schema_version` » suppose qu'on liste les fichiers ET la table puis on diff. Le runner doit charger les deux ensembles avant toute écriture. Ordre des vérifs documenté dans le script.

## Out of scope
- Logique applicative Redis (cache retrieval, pub/sub).
- Runner migrations applicatif (Rust `sqlx migrate` ou Python).
- Down migrations / rollback.
- Gate CI bloquant sur SLA boot.
- Mesure boot en Cloud Run.
- Provisioning Terraform Redis managé.
- Tuning des healthchecks postgres/workers/gateway au-delà du nécessaire.
- Port Redis exposé sur l'hôte.
- `make migrate-status`.
- Stockage GCS des artefacts boot.
- Refonte globale `docs/runbook.md` (uniquement section SLA boot + section Migrations DB).
