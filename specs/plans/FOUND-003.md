# Plan — FOUND-003 Schéma DB walking skeleton

## Résumé
Migration unique `0002_schema.sql` qui crée les six tables fondatrices (`users`, `documents`, `chunks`, `conversations`, `tickets`, `query_log`), pose l'index HNSW cosine sur `chunks.embedding`, insère la ligne sentinel `users` UUID nul, et enregistre `schema_version (2, 'schema walking skeleton')` via le runner FOUND-002. Tests d'intégration `psql`-only valident introspection, contraintes, FK, CHECK, dimension vector, idempotence.

## Hypothèses résolues

- **H1 — Conflit AC-14 vs AC-16 sur description** : AC-16 fige le nom de fichier à `0002_schema.sql` mais le runner FOUND-002 dérive `description` du slug filename (`schema` au lieu de `schema walking skeleton` exigé par AC-14). **Résolution** : étendre `migrations/run.sh` pour lire une directive magique en première ligne du fichier SQL (`-- description: <text>`) et l'utiliser à la place du slug si présente. Fallback inchangé. Le fichier `0002_schema.sql` commence par `-- description: schema walking skeleton`. Touche `migrations/run.sh` (PAS humain-only) ; le fichier `migrations/0002_schema.sql` lui-même est humain-only — l'implementer rédige le SQL et **présente à l'humain pour approbation explicite avant commit**.
- **H2 — Cascade conversations→tickets** : AC-7 spécifie `ON DELETE RESTRICT` (preuve AC-8 SQLSTATE 23503). **Résolution** : `tickets.conversation_id ... REFERENCES conversations(id) ON DELETE RESTRICT` (explicite, pas de défaut implicite).
- **H3 — Cascade query_log→users / query_log→conversations** : AC-9 ne précise pas. **Résolution** : `query_log.user_id ... REFERENCES users(id)` sans clause cascade (défaut `NO ACTION` ≡ RESTRICT). `query_log.conversation_id ... REFERENCES conversations(id) ON DELETE SET NULL` (cohérent avec NULL autorisé + audit trail préservé). Justification : audit log doit survivre suppression d'une conversation tout en gardant l'user_id pour usage admin futur.
- **H4 — Cascade conversations→users** : AC-6 ne précise pas. **Résolution** : `conversations.user_id ... REFERENCES users(id)` sans cascade (NO ACTION). Cohérent avec AC-8 (audit-trail-first). Sentinel user `00000000-...` ne sera jamais supprimé en pratique.
- **H5 — UUID nul littéral** : `'00000000-0000-0000-0000-000000000000'::uuid` accepté par PostgreSQL. **Résolution** : utiliser cette forme dans `INSERT INTO users (id, tier) VALUES ('00000000-0000-0000-0000-000000000000', 'anonymous') ON CONFLICT (id) DO NOTHING;` — ON CONFLICT garantit AC-15 (ré-exécution idempotente même si la migration est rejouée hors transaction, défense en profondeur).
- **H6 — HNSW opclass et paramètres** : AC-5 fixe `vector_cosine_ops` + méthode `hnsw`. **Résolution** : `CREATE INDEX chunks_embedding_idx ON chunks USING hnsw (embedding vector_cosine_ops);` — paramètres `m` / `ef_construction` aux valeurs par défaut pgvector (cf spec AC perf §82).
- **H7 — Tests d'intégration : Rust ou Python ou bash** : FOUND-002 a posé un harnais bash `tests/migrations/run_tests.sh`. **Résolution** : étendre ce même harnais (`tests/migrations/run_tests.sh`) avec les scénarios AC-1 à AC-15. Aucune dépendance Rust/Python introduite. `psql` + `jq` suffisent. Cohérent avec le pattern FOUND-002 et évite des changements `Cargo.toml` / `pyproject.toml`.
- **H8 — Property test INV-7** : `specs/properties.md` INV-7 cible `workers/tests/test_ticket_creation.py` (FK conversation_id). **Résolution** : ce ticket pose la FK DB, pas le code worker. Le test property arrivera avec le ticket workers qui crée les tickets (out of scope ici). AC-8 couvre la garantie DB en intégration. Aucun test hypothesis ajouté ici.
- **H9 — Schemathesis / OpenAPI** : pas de touche au contrat REST. **Résolution** : aucune modification `specs/openapi/gateway-to-workers.yml`, pas de schemathesis run.
- **H10 — Sentinel `created_at`** : AC-2 exige id + tier exactement. **Résolution** : insert ne précise que `(id, tier)`, `created_at` prend le DEFAULT `NOW()`.

## Files to touch

- `migrations/0002_schema.sql` — **humain-only, approbation explicite requise** : DDL six tables, contraintes CHECK / UNIQUE / FK avec cascades, index HNSW, INSERT sentinel users, première ligne `-- description: schema walking skeleton`.
- `migrations/run.sh` — extension : parser la directive `-- description: <text>` en tête de fichier et l'utiliser pour l'INSERT `schema_version`. Fallback slug filename inchangé.
- `tests/migrations/run_tests.sh` — extension : nouveaux scénarios `test_0002_schema_*` couvrant AC-1 à AC-15.
- `tests/migrations/fixtures/expected_users_columns.txt` — snapshot colonnes attendues `users` (oracle introspection AC-1).
- `tests/migrations/fixtures/expected_documents_columns.txt` — idem `documents` (AC-3).
- `tests/migrations/fixtures/expected_chunks_columns.txt` — idem `chunks` (AC-4).
- `tests/migrations/fixtures/expected_conversations_columns.txt` — idem (AC-6).
- `tests/migrations/fixtures/expected_tickets_columns.txt` — idem (AC-7).
- `tests/migrations/fixtures/expected_query_log_columns.txt` — idem (AC-9).
- `CHANGELOG.md` — entrée `## [Unreleased]` : `feat(db): schema walking skeleton (users, documents, chunks, conversations, tickets, query_log) + HNSW index`.

## Order of implementation (TDD)

1. **Étendre runner** (`migrations/run.sh`) : lire première ligne du fichier SQL, détecter `^-- description: (.+)$`, capturer en variable `description`. Si absent → fallback comportement actuel (slug). Test bash unitaire ad hoc dans `tests/migrations/run_tests.sh`.
2. **Écrire fixtures attendues** (`tests/migrations/fixtures/*.txt`) : snapshots issus de l'introspection `\d+ <table>` ou requêtes `information_schema.columns` triées canoniquement.
3. **Écrire tests d'intégration** (`tests/migrations/run_tests.sh` extension) référençant chaque AC en commentaire (AC-1 à AC-15). Les tests échouent (red) car migration absente.
4. **Rédiger `migrations/0002_schema.sql`** — humain-only : DDL complète, INSERT sentinel `ON CONFLICT (id) DO NOTHING`, première ligne directive description. **Implementer prépare le SQL et le présente à l'humain pour validation avant `git add`.**
5. **Lancer `make migrate`** localement sur base vierge → migration appliquée → `tests/migrations/run_tests.sh` passe (green).
6. **Lancer `make migrate` deux fois** → AC-15 vérifié (idempotence + log `already applied`).
7. **CHANGELOG** entrée `[Unreleased]`.
8. **Property test** : aucun (cf H8).
9. **OpenAPI** : aucun (cf H9).
10. **Diff review** : `cargo` / `ruff` / `mypy` non touchés (rien dans gateway / workers).

## Acceptance criteria mapping

| AC | Test |
|---|---|
| AC-1 | `tests/migrations/run_tests.sh` scénario `test_0002_users_schema` : `psql -c "SELECT column_name, data_type, is_nullable, column_default FROM information_schema.columns WHERE table_name='users' ORDER BY ordinal_position;"` diff vs `expected_users_columns.txt` + `pg_constraint` CHECK tier IN. |
| AC-2 | `test_0002_users_sentinel` : `psql -c "SELECT id, tier FROM users;"` → exactement 1 ligne `('00000000-0000-0000-0000-000000000000', 'anonymous')`. |
| AC-3 | `test_0002_documents_schema` : introspection vs `expected_documents_columns.txt` + CHECK `access_tier`. |
| AC-4 | `test_0002_chunks_schema` : introspection vs `expected_chunks_columns.txt` + `pg_constraint` (FK ON DELETE CASCADE + UNIQUE composite `document_id, ord`). |
| AC-5 | `test_0002_chunks_hnsw_index` : `SELECT indexname, indexdef FROM pg_indexes WHERE tablename='chunks' AND indexname='chunks_embedding_idx';` assert `USING hnsw` + `vector_cosine_ops`. |
| AC-6 | `test_0002_conversations_schema` : introspection vs fixture. |
| AC-7 | `test_0002_tickets_schema` : introspection vs fixture + CHECK status + CHECK priority_score >= 1. |
| AC-8 | `test_0002_tickets_fk_restrict` : INSERT users → conversations → tickets ; `DELETE FROM conversations WHERE id=$1` → assert SQLSTATE `23503` ; `SELECT COUNT(*) FROM conversations` reste 1. |
| AC-9 | `test_0002_query_log_schema` : introspection vs fixture + CHECK mode + CHECK latency_ms. |
| AC-10 | `test_0002_query_log_indexes` : `pg_indexes` assert présence `query_log_user_created_idx` (`(user_id, created_at DESC)`) + `query_log_created_idx` (`(created_at)`). |
| AC-11 | `test_0002_chunks_dimension_check` : `INSERT INTO chunks (...) VALUES (..., '[0,0,...]'::vector(512), ...)` → assert exit non-zéro + stderr contient `expected 1024 dimensions`. |
| AC-12 | `test_0002_tickets_status_check` : INSERT ticket status='unknown' → assert SQLSTATE `23514`. |
| AC-13 | `test_0002_users_tier_check` : INSERT user tier='admin' → assert SQLSTATE `23514`. |
| AC-14 | `test_0002_schema_version_row` : `SELECT version, description FROM schema_version WHERE version=2;` → exactement `(2, 'schema walking skeleton')`. |
| AC-15 | `test_0002_idempotent` : double `bash migrations/run.sh` → exit 0 sur 2e run + log contient `migration 2 already applied, skipping` + sentinel users count = 1. |
| AC-16 | `test_0002_filename_regex` : `bash` regex `^[0-9]{4}_[a-z0-9_]+\.sql$` sur `0002_schema.sql`. |

## Out of scope

- Auth columns sur `users` (`email`, `password_hash`, `oauth_subject_id`, `provider`) → SEC-*.
- Table `sessions` → SEC-*.
- Partitionnement `query_log` + job purge 30j → OBS-*.
- Triggers `BEFORE UPDATE` sur `updated_at` → OBS-*.
- Seed de documents / chunks / conversations / tickets → ING-001 et aval.
- Index complémentaires (full-text titre, GIN tags, etc.) → tickets aval à la demande.
- Down migrations / rollback (cohérent FOUND-002).
- Rôle DB séparé app vs migrator → SEC-*.
- ENUM PostgreSQL (gardé TEXT + CHECK).
- Property test hypothesis INV-7 (porté par ticket worker créant les tickets).
- Toute modification gateway/ ou workers/ (aucun code Rust ni Python ce ticket).
- Toute modification OpenAPI / contrat REST.

## Estimated diff size

- `migrations/0002_schema.sql` : ~110 lignes (exclu du quota par règle vertical-slice).
- `migrations/run.sh` : +8 lignes (parsing directive description).
- `tests/migrations/run_tests.sh` : +180 lignes (15 scénarios + helpers).
- `tests/migrations/fixtures/*.txt` : ~60 lignes cumulées (snapshots, exclus du quota générés).
- `CHANGELOG.md` : +2 lignes.

**Total compté quota** : ~190 lignes ≤ 300 LOC. Conforme vertical-slice.
