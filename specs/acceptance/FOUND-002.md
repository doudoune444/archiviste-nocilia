# FOUND-002 — Stack locale reproductible & SLA boot

## Contexte

Le scaffolding FOUND-001 démarre gateway + workers + postgres mais ne couvre ni cache Redis, ni discipline de migrations versionnées, ni budget de démarrage mesuré. Sans ces fondations, l'environnement local diverge entre machines et la régression de temps de boot passe inaperçue. Ce ticket fige la stack locale, la procédure de migration, et instrumente le SLA de démarrage en CI.

## Critères d'acceptation

- AC-1 : `docker compose up -d` démarre les services `postgres`, `redis`, `workers`, `gateway`, et chacun atteint l'état `healthy` (visible via `docker compose ps --format json`).
- AC-2 : Le service `redis` exige le mot de passe lu depuis la variable d'environnement `REDIS_PASSWORD` ; une connexion `redis-cli -a $REDIS_PASSWORD PING` répond `PONG`, une connexion sans `-a` est rejetée par le serveur.
- AC-3 : Le service `redis` persiste ses données via le volume nommé `redis-data` monté sur `/data` avec AOF activé (`--appendonly yes`) ; après `docker compose restart redis`, une clé écrite avant le restart est toujours lisible.
- AC-4 : Le fichier `.env.example` est versionné et déclare au minimum les clés `REDIS_PASSWORD` et `DATABASE_URL` ; `.env` est listé dans `.gitignore`.
- AC-5 : Les migrations dans `migrations/` suivent le nommage `NNNN_description.sql` (4 chiffres monotones, ex. `0001_init.sql`) ; la version appliquée est extraite du nom de fichier.
- AC-6 : `make migrate` exécute un conteneur jetable `docker compose run --rm migrator` (image `postgres:16`, `./migrations:/migrations:ro`, `DATABASE_URL` lu depuis `.env`) qui applique en ordre croissant chaque fichier de version absente de la table `schema_version`, puis insère la ligne `(version, description, applied_at)` correspondante.
- AC-7 : Si une version `N` est déjà présente dans `schema_version`, le runner saute le fichier et émet un log `migration N already applied, skipping`.
- AC-8 : Si un fichier de version `N` est absent de `schema_version` mais que `MAX(version) > N` en base, le runner sort en code non-zéro avec le message `migration gap: file version N missing from schema_version while higher version applied` et n'applique aucune migration.
- AC-9 : Un script `scripts/measure-boot.sh` (ou équivalent documenté) vérifie via `docker image inspect <image>` la présence des images des 4 services AVANT `docker compose up -d` ; toute image manquante provoque un exit code non-zéro avec le message `Image <name> missing. Run 'docker compose build' first.`.
- AC-10 : Le même script mesure le temps écoulé entre `docker compose up -d` et le moment où les 4 services sont `healthy`, et émet un artefact JSON conforme au schéma fixé en annexe (champs `total_seconds`, `sla_seconds`, `passed`, `services[].name`, `services[].healthy_at_seconds`).
- AC-11 : Le SLA `total_seconds <= sla_seconds` avec `sla_seconds = 30` détermine la valeur du booléen `passed` ; le script sort en code 0 quel que soit `passed` (mesure non-bloquante).
- AC-12 : Un workflow GitHub Actions déclenché sur `pull_request` et `push` vers `main` exécute `docker compose build` puis `scripts/measure-boot.sh`, archive l'artefact JSON via `actions/upload-artifact` (rétention 30 jours), et utilise `continue-on-error: true` sur l'étape de mesure.

## Non-goals

- Pas de logique applicative côté Redis (pas de pub/sub, pas de cache de retrieval).
- Pas de runner de migrations en Rust ou Python applicatif (le runner est un one-shot conteneur jetable).
- Pas de rollback / down migrations.
- Pas de gate CI bloquant sur le SLA boot (introduit dans un ticket ultérieur quand baseline stable).
- Pas de mesure de boot en environnement Cloud Run.
- Pas de provisioning Terraform de Redis managé.
- Pas de tuning des healthchecks existants (postgres, workers, gateway) au-delà du strict nécessaire.

## Pre-conditions

- FOUND-001 mergé : `docker-compose.yml` existant avec `postgres`, `workers`, `gateway` healthy.
- Image `postgres:16` accessible (déjà tirée pour `pgvector/pgvector:pg16` ou pull additionnel autorisé).
- Make disponible sur la machine de dev (documenté dans `CLAUDE.md` ou ajouté au pré-requis).

## Failure modes

- Image conteneur manquante au moment du `measure-boot` → exit code non-zéro, message `Image <name> missing. Run 'docker compose build' first.`, aucun service démarré.
- `REDIS_PASSWORD` absent / vide au boot du service `redis` → service échoue le healthcheck, log `redis: REDIS_PASSWORD required`, statut `unhealthy`.
- Fichier migration mal nommé (ne matche pas `^[0-9]{4}_[a-z0-9_]+\.sql$`) → runner exit non-zéro, message `invalid migration filename: <name>`, aucune migration appliquée.
- Gap de version (cf AC-8) → exit non-zéro, message explicite, base inchangée.
- Erreur SQL pendant l'application d'une migration → transaction rollback, exit non-zéro, message `migration N failed: <db error>`, ligne `schema_version` non insérée pour cette version.
- Timeout SLA boot dépassé → `passed: false` dans l'artefact, exit code 0 (non-bloquant), workflow CI marqué neutre via `continue-on-error`.

## Touch points (informatif, non contraignant pour l'architect)

- `docker-compose.yml` — ajout service `redis`, volume `redis-data`, service `migrator`.
- `.env.example` — clés `REDIS_PASSWORD`, `DATABASE_URL`.
- `.gitignore` — entrée `.env` (vérifier qu'elle existe).
- `Makefile` — cible `migrate`.
- `migrations/` — convention de nommage, runner script (`migrations/run.sh` ou équivalent).
- `scripts/measure-boot.sh` — orchestration mesure + artefact JSON.
- `.github/workflows/boot-sla.yml` — workflow CI dédié (ou step ajouté à `ci.yml`).

## Test oracle

- AC-1 : intégration · `docker compose ps --format json | jq` après `up -d`, assert `Health: healthy` pour chaque service.
- AC-2 : intégration · `docker compose exec redis redis-cli -a "$REDIS_PASSWORD" PING` retourne `PONG` ; `docker compose exec redis redis-cli PING` retourne `NOAUTH Authentication required.`.
- AC-3 : intégration · scénario set/restart/get sur clé Redis, assert valeur conservée.
- AC-4 : contract · `test -f .env.example && grep -q '^REDIS_PASSWORD=' .env.example && grep -q '^DATABASE_URL=' .env.example` ; `git check-ignore .env` retourne `.env`.
- AC-5 : contract · script de validation parse chaque nom dans `migrations/` contre regex `^[0-9]{4}_[a-z0-9_]+\.sql$`.
- AC-6 : intégration · `make migrate` sur base vierge, assert chaque version présente dans `schema_version` après run.
- AC-7 : intégration · `make migrate` ré-exécuté, assert log `already applied` pour versions existantes, exit 0.
- AC-8 : intégration · base avec version `0002` mais fichier `0001` absent, assert exit non-zéro et message gap exact.
- AC-9 : intégration · supprimer une image locale via `docker image rm`, lancer `scripts/measure-boot.sh`, assert exit non-zéro et message exact.
- AC-10 : contract · validation de l'artefact JSON contre schéma fixe (clés, types, présence des 4 services).
- AC-11 : intégration · forcer `sla_seconds=1`, assert `passed: false` dans l'artefact ET exit 0.
- AC-12 : contract · GitHub Actions run vert sur PR de feature, artefact JSON présent, étape `measure` marquée `continue-on-error`.

## Performance / SLO

- SLA boot local : `total_seconds <= 30s` sur machine dev de référence (CPU 4 cœurs, RAM 8 GiB) avec images pré-buildées. Mesure non-bloquante en CI initialement.
- Migration runner : `make migrate` sur base vierge (1 migration) termine en `< 5s` hors temps de pull image.

## Security / trust boundary

- `REDIS_PASSWORD` : valeur traitée comme secret. Jamais loggée. `.env` ignoré par git (cf `.claude/rules/secret-hygiene.md`).
- Volume `./migrations:/migrations:ro` : montage lecture seule pour le conteneur migrator.
- Service `redis` : pas de port exposé sur l'hôte hors localhost (`127.0.0.1:6379:6379` ou réseau interne uniquement) — à clarifier en open question.
- Aucun secret en clair dans `docker-compose.yml` ni `.env.example` (placeholders uniquement).

## Observability

- Runner migration : log structuré `migration <version> applied in <ms>ms` ou `migration <version> already applied, skipping`.
- Script measure-boot : artefact JSON archivé (artefact CI), pas de log additionnel requis cette itération.
- Pas de métrique OpenTelemetry exposée à ce stade (réservé tickets OBS-*).

## Estimation d'effort

M

## Annexe — schéma artefact boot

```json
{
  "total_seconds": 24.3,
  "sla_seconds": 30,
  "passed": true,
  "services": [
    {"name": "postgres", "healthy_at_seconds": 8.1},
    {"name": "redis", "healthy_at_seconds": 2.4},
    {"name": "workers", "healthy_at_seconds": 18.7},
    {"name": "gateway", "healthy_at_seconds": 24.3}
  ]
}
```

## Open questions

- OQ-1 : Le service `redis` doit-il exposer son port sur l'hôte (`6379:6379`) pour debug local, ou rester confiné au réseau Docker interne ? Implication sécurité (surface d'attaque locale) vs. confort dev.
- OQ-2 : Le service `migrator` doit-il être déclaré dans `docker-compose.yml` avec `profiles: ["tools"]` (n'est pas démarré par `up -d` par défaut) ou rester invoqué uniquement via `docker compose run --rm` ? Préférence sur la lisibilité du compose.
- OQ-3 : Le SLA `30s` est-il calibré sur quelle machine de référence ? Faut-il documenter le hardware cible dans `docs/runbook.md` ou accepter une variance CI vs local sans baseline ?
- OQ-4 : Le workflow CI doit-il vivre dans un fichier dédié `boot-sla.yml` ou être un job ajouté à `ci.yml` existant (créé en FOUND-001) ? Impact sur la lisibilité des runs.
- OQ-5 : En cas d'échec d'une migration en cours (AC failure mode SQL), la transaction est-elle par fichier ou globale (toutes les migrations en attente dans une seule transaction) ? Impact sur l'atomicité d'un batch.
- OQ-6 : Faut-il une commande `make migrate-status` (lecture seule) qui liste fichiers vs `schema_version` sans appliquer, pour debug ? Scope ce ticket ou ticket séparé ?
- OQ-7 : La rétention de 30 jours de l'artefact JSON est-elle suffisante, ou faut-il pousser vers un stockage long-terme (GCS) pour analyse historique ? Probablement ticket OBS-* séparé.

## Status

draft
