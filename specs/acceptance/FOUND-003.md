# FOUND-003 — Schéma DB walking skeleton (documents/chunks/conversations/tickets/query_log/users)

## Contexte

FOUND-002 a posé l'extension `vector` + `pgcrypto` et le runner de migrations versionnées, mais aucune table applicative n'existe. Sans schéma, les tickets aval (ING-001 ingestion chunks, ING-003 index conversations, GEN-* tickets/query_log, SEC-* users-tier) ne peuvent pas démarrer en parallèle. Ce ticket fige les six tables fondatrices du RAG dans une migration unique `0002_schema.sql` qui constitue le squelette persistant du walking skeleton.

## Critères d'acceptation

- AC-1 : Après `make migrate` sur base vierge, la table `users` existe avec colonnes `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`, `tier TEXT NOT NULL` (CHECK contrainte aux valeurs `anonymous`, `member`, `author`), `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`.
- AC-2 : Après `make migrate` sur base vierge, la table `users` contient exactement une ligne sentinel `(id='00000000-0000-0000-0000-000000000000', tier='anonymous')` insérée par la migration.
- AC-3 : La table `documents` existe avec colonnes `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`, `source_path TEXT NOT NULL UNIQUE`, `title TEXT NOT NULL`, `tags TEXT[] NOT NULL DEFAULT '{}'`, `access_tier TEXT NOT NULL` (CHECK ∈ `public`, `members`, `author_only`, défaut `public`), `content_hash TEXT NOT NULL`, `ingested_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`.
- AC-4 : La table `chunks` existe avec colonnes `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`, `document_id UUID NOT NULL REFERENCES documents(id) ON DELETE CASCADE`, `ord INT NOT NULL`, `text TEXT NOT NULL`, `embedding vector(1024) NOT NULL`, contrainte `UNIQUE (document_id, ord)`.
- AC-5 : Un index ANN nommé `chunks_embedding_idx` existe sur `chunks(embedding)` utilisant la méthode `hnsw` avec opclass `vector_cosine_ops` (vérifiable via `pg_indexes` + `pg_index.indam`).
- AC-6 : La table `conversations` existe avec colonnes `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`, `user_id UUID NOT NULL REFERENCES users(id)`, `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`, `gcs_uri TEXT NOT NULL UNIQUE`, `message_count INT NOT NULL DEFAULT 0` (CHECK `message_count >= 0`).
- AC-7 : La table `tickets` existe avec colonnes `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`, `conversation_id UUID NOT NULL REFERENCES conversations(id) ON DELETE RESTRICT`, `question TEXT NOT NULL`, `category TEXT NOT NULL DEFAULT 'uncategorized'`, `priority_score INT NOT NULL DEFAULT 1` (CHECK `priority_score >= 1`), `status TEXT NOT NULL` (CHECK ∈ `open`, `resolved`, `dismissed`, défaut `open`), `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`, `updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`.
- AC-8 : Tenter `DELETE FROM conversations WHERE id = $1` alors qu'un ticket référence cette conversation échoue avec une erreur PostgreSQL `foreign_key_violation` (SQLSTATE `23503`) et la ligne reste en base.
- AC-9 : La table `query_log` existe avec colonnes `id UUID PRIMARY KEY DEFAULT gen_random_uuid()`, `request_id UUID NOT NULL UNIQUE`, `user_id UUID NOT NULL REFERENCES users(id)`, `conversation_id UUID NULL REFERENCES conversations(id)`, `query_text TEXT NOT NULL`, `intent TEXT NULL`, `mode TEXT NULL` (CHECK `mode IS NULL OR mode IN ('canon','off_topic','lore_gap','mystery')`), `status_code INT NOT NULL`, `latency_ms INT NOT NULL` (CHECK `latency_ms >= 0`), `prompt_tokens INT NULL`, `completion_tokens INT NULL`, `cost_eur NUMERIC(10,6) NULL`, `created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()`.
- AC-10 : Deux index existent sur `query_log` : `query_log_user_created_idx` sur `(user_id, created_at DESC)` et `query_log_created_idx` sur `(created_at)`.
- AC-11 : Insérer dans `chunks` une ligne avec `embedding` de dimension ≠ 1024 échoue avec une erreur PostgreSQL renvoyée par l'extension `vector` (message contenant `expected 1024 dimensions`) et la ligne n'est pas persistée.
- AC-12 : Insérer dans `tickets` une ligne avec `status` hors de l'ensemble autorisé échoue avec SQLSTATE `23514` (check_violation) et la ligne n'est pas persistée.
- AC-13 : Insérer dans `users` une ligne avec `tier` hors de l'ensemble autorisé échoue avec SQLSTATE `23514` (check_violation) et la ligne n'est pas persistée.
- AC-14 : Après `make migrate`, la table `schema_version` contient une ligne `(version=2, description='schema walking skeleton')` (description exacte à figer dans l'implémentation).
- AC-15 : `make migrate` ré-exécuté sur une base déjà migrée sort en code 0, log `migration 2 already applied, skipping`, et n'altère ni les tables ni la ligne sentinel `users`.
- AC-16 : La migration `0002_schema.sql` réside sous `migrations/` et matche le regex de nommage défini par FOUND-002 (`^[0-9]{4}_[a-z0-9_]+\.sql$`).

## Non-goals

- Pas de table `sessions` (cookie session server-side) — ticket SEC-* dédié.
- Pas de colonnes auth (`email`, `password_hash`, `oauth_subject_id`, `provider`) sur `users` — phase 1 squelette uniquement, ajoutées par ticket SEC-* quand login est introduit.
- Pas de partitionnement `query_log` ni de job de purge 30j — ticket OBS-* futur (l'index `created_at` est posé pour rendre la purge cheap).
- Pas de seed de documents / chunks / conversations / tickets — données ajoutées par ING-001 et tickets aval.
- Pas d'index complémentaires (full-text sur `documents.title`, GIN sur `documents.tags`, etc.) — ajoutés à la demande quand un ticket aval en a besoin.
- Pas de migrations down / rollback (cohérent avec FOUND-002).
- Pas de rôle DB séparé app-only vs migrator — couvert par D-T-1 du threat model dans un ticket SEC-* dédié.
- Pas de stockage de titre/résumé de conversation en DB — dérivé du Markdown au read time (vision §39).
- Pas d'ENUM PostgreSQL pour `tier`, `access_tier`, `mode`, `status`, `category` — TEXT + CHECK en phase 1 (taxonomies non figées).
- Pas de colonnes d'audit (`created_by`, `deleted_at`) ni soft-delete — reportés à un ticket dédié si nécessaire.

## Pre-conditions

- FOUND-002 mergé : runner `make migrate` opérationnel, `0001_init.sql` applique `vector` + `pgcrypto` + table `schema_version`.
- Image `pgvector/pgvector:pg16` disponible dans `docker-compose.yml` (HNSW disponible nativement avec pgvector ≥ 0.5.0, packagée avec cette image).

## Failure modes

- Embedding de dimension ≠ 1024 inséré dans `chunks` → erreur extension `vector`, message `expected 1024 dimensions`, ligne non persistée.
- Violation FK `chunks.document_id` → SQLSTATE `23503` (foreign_key_violation), ligne non persistée.
- Suppression d'une `conversation` référencée par un `ticket` → SQLSTATE `23503`, suppression refusée, audit trail préservé (cf AC-8).
- Suppression d'un `document` → cascade automatique sur `chunks` (ON DELETE CASCADE), aucun chunk orphelin.
- Insertion `users.tier` / `tickets.status` / `documents.access_tier` / `query_log.mode` hors ensemble CHECK → SQLSTATE `23514` (check_violation), ligne non persistée.
- Doublon `documents.source_path` / `chunks (document_id, ord)` / `conversations.gcs_uri` / `query_log.request_id` → SQLSTATE `23505` (unique_violation), ligne non persistée.
- `make migrate` interrompu en cours d'application de `0002` → transaction rollback (cf FOUND-002 AC-8), `schema_version` ne contient pas la ligne version=2, ré-exécution applique proprement.

## Touch points (informatif, non contraignant pour l'architect)

- `migrations/0002_schema.sql` — DDL des six tables, contraintes, index HNSW, insert sentinel.
- `specs/properties.md` — INV-7 (FK conversation_id) reste valide ; aucune modification attendue.
- `docs/runbook.md` — éventuellement noter la dimension d'embedding lockée (1024, bge-m3) pour les opérateurs.

## Test oracle

- AC-1 : intégration · requête `information_schema.columns` et `pg_constraint` sur `users`, assert colonnes + types + CHECK.
- AC-2 : intégration · `SELECT id, tier FROM users` après migration, assert exactement une ligne sentinel UUID nul + tier `anonymous`.
- AC-3 : intégration · introspection `documents` (colonnes, types, defaults, CHECK `access_tier`).
- AC-4 : intégration · introspection `chunks` (colonnes, types, FK `ON DELETE CASCADE`, UNIQUE composite).
- AC-5 : intégration · `SELECT indexname, indexdef FROM pg_indexes WHERE tablename='chunks'` assert présence `chunks_embedding_idx` USING hnsw + opclass `vector_cosine_ops`.
- AC-6 : intégration · introspection `conversations`.
- AC-7 : intégration · introspection `tickets`.
- AC-8 : intégration · scénario : insérer user + conversation + ticket → `DELETE FROM conversations WHERE id=$1` doit lever SQLSTATE `23503`, `SELECT COUNT(*)` sur conversations reste 1.
- AC-9 : intégration · introspection `query_log` (colonnes, nullabilité, CHECK `mode`, CHECK `latency_ms >= 0`).
- AC-10 : intégration · `pg_indexes` assert présence des deux index nommés sur `query_log`.
- AC-11 : intégration · tentative INSERT chunk avec `embedding` de dimension 512 → attendu erreur runtime `vector`.
- AC-12 : intégration · INSERT ticket avec `status='unknown'` → SQLSTATE `23514`.
- AC-13 : intégration · INSERT user avec `tier='admin'` → SQLSTATE `23514`.
- AC-14 : intégration · `SELECT version, description FROM schema_version WHERE version=2` après migration.
- AC-15 : intégration · double `make migrate`, assert exit 0, log `already applied`, comparaison snapshot des tables identique.
- AC-16 : contract · regex sur le nom de fichier dans `migrations/`.

## Performance / SLO

- Application de `0002_schema.sql` sur base vierge : `< 5s` hors temps de pull image (cohérent avec budget FOUND-002 sur runner migrations).
- Index HNSW build sur table vide : négligeable. Pour des volumes ultérieurs, paramètres `m` / `ef_construction` laissés aux valeurs par défaut pgvector phase 1 — réévaluation dans un ticket RET-* quand le corpus dépasse 10k chunks.

## Security / trust boundary

- DDL exécutée par le rôle migrator (FOUND-002), pas par le rôle applicatif. Le ticket SEC-* dédié séparera les deux rôles ; ce ticket assume rôle unique en phase 1.
- Aucun secret en clair dans la migration. Ligne sentinel `users` utilise UUID nul fixe (`00000000-0000-0000-0000-000000000000`), pas de PII.
- Dimension d'embedding `1024` lockée par CHECK implicite du type `vector(1024)` : empêche un upstream (ING-001) d'injecter des vecteurs de modèle différent sans migration explicite.
- IDOR (G-E-1 du threat model) : la FK `conversations.user_id NOT NULL` est posée pour permettre aux handlers gateway d'enforcer `WHERE user_id = $auth`. Ce ticket pose la fondation, pas l'enforcement runtime.

## Observability

- Aucun log additionnel hors logs du runner migrations FOUND-002.
- Aucune métrique OpenTelemetry exposée (réservé tickets OBS-*).

## Effort estimate

M

## Decisions résolues (humain, 2026-05-05)

- UUIDv4 DB-side via `DEFAULT gen_random_uuid()` sur toutes les colonnes `id` (chunks, documents, users, conversations, tickets, query_log). UUIDv7 reporté à un ticket futur si menace G-E-1 le justifie post-MVP.
- Pas de trigger `BEFORE UPDATE` sur `updated_at` — set applicatif explicite côté workers/gateway. Trigger automatique = ticket OBS-* dédié si besoin.
- `tickets.category` garde `DEFAULT 'uncategorized'` (taxonomie figée plus tard).
- `query_log.cost_eur NUMERIC(10,6)` confirmé suffisant phase MVP (Mistral Small ~0.3€/Mtok, query unique = micro-cents, headroom 4 digits avant virgule).

## Status

ready
