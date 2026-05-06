#!/usr/bin/env bash
# Integration tests for migrations/run.sh -- covers AC-7..AC-10 (FOUND-002) and
# AC-1..AC-16 (FOUND-003 schema walking skeleton).
#
# Requires: docker. Uses pgvector/pgvector:pg16 image so the chunks vector(1024)
# column and the hnsw index from 0002_schema.sql build natively.
set -euo pipefail

cd "$(dirname "$0")"

CONTAINER="archiviste-mig-test-$$"
NETWORK="archiviste-mig-net-$$"
PASSWORD="testpass"
PG_IMAGE="pgvector/pgvector:pg16"

cleanup() {
  docker rm -f "$CONTAINER" >/dev/null 2>&1 || true
  docker network rm "$NETWORK" >/dev/null 2>&1 || true
  rm -rf "$TMPDIR_TEST"
}
trap cleanup EXIT

TMPDIR_TEST=$(mktemp -d)
SCENARIO_DIR="$TMPDIR_TEST/migrations"
mkdir -p "$SCENARIO_DIR"

docker network create "$NETWORK" >/dev/null
docker run -d --name "$CONTAINER" --network "$NETWORK" \
  -e POSTGRES_PASSWORD="$PASSWORD" -e POSTGRES_DB=archiviste \
  "$PG_IMAGE" >/dev/null

for _ in $(seq 1 30); do
  if docker exec "$CONTAINER" pg_isready -U postgres >/dev/null 2>&1; then break; fi
  sleep 1
done

run_runner() {
  docker run --rm --network "$NETWORK" \
    -e DATABASE_URL="postgres://postgres:${PASSWORD}@${CONTAINER}:5432/archiviste" \
    -v "$1:/migrations:ro" \
    --entrypoint /bin/bash "$PG_IMAGE" /migrations/run.sh
}

psql_db() {
  docker exec -e PGPASSWORD="$PASSWORD" "$CONTAINER" psql -U postgres -d archiviste -tAc "$1"
}

assert_eq() {
  if [[ "$1" != "$2" ]]; then
    echo "FAIL: expected [$2] got [$1] ($3)" >&2
    exit 1
  fi
}

REPO_ROOT=$(cd ../.. && pwd)
cp "$REPO_ROOT/migrations/run.sh" "$SCENARIO_DIR/run.sh"

# Scenario 1: fresh DB, two valid migrations -> both applied (AC-7).
cat > "$SCENARIO_DIR/0001_init.sql" <<'SQL'
CREATE TABLE thing (id INT);
SQL
cat > "$SCENARIO_DIR/0002_widget.sql" <<'SQL'
CREATE TABLE widget (id INT);
SQL

run_runner "$SCENARIO_DIR"
count=$(psql_db "SELECT COUNT(*) FROM schema_version;")
assert_eq "$count" "2" "AC-7 fresh apply count"

# Scenario 2: re-run -> skip both, exit 0 (AC-9).
output=$(run_runner "$SCENARIO_DIR")
echo "$output" | grep -q "migration 1 already applied, skipping" || { echo "FAIL AC-9 v1"; exit 1; }
echo "$output" | grep -q "migration 2 already applied, skipping" || { echo "FAIL AC-9 v2"; exit 1; }

# Scenario 3: bad SQL on version 3 -> rollback v3, v1/v2 intact (AC-8).
cat > "$SCENARIO_DIR/0003_bad.sql" <<'SQL'
CREATE TABLE good (id INT);
THIS_IS_NOT_VALID_SQL;
SQL
if run_runner "$SCENARIO_DIR" >/dev/null 2>&1; then
  echo "FAIL AC-8: runner did not exit non-zero"; exit 1
fi
v2_present=$(psql_db "SELECT EXISTS (SELECT 1 FROM schema_version WHERE version = 2);")
v3_absent=$(psql_db "SELECT NOT EXISTS (SELECT 1 FROM schema_version WHERE version = 3);")
assert_eq "$v2_present" "t" "AC-8 v2 retained"
assert_eq "$v3_absent"  "t" "AC-8 v3 rolled back"
good_absent=$(psql_db "SELECT NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='good');")
assert_eq "$good_absent" "t" "AC-8 inner statement rolled back"
rm "$SCENARIO_DIR/0003_bad.sql"

# Scenario 4: gap detection (AC-10).
psql_db "INSERT INTO schema_version (version, description) VALUES (5, 'fake gap');" >/dev/null
cat > "$SCENARIO_DIR/0003_late.sql" <<'SQL'
CREATE TABLE late (id INT);
SQL
set +e
output=$(run_runner "$SCENARIO_DIR" 2>&1)
rc=$?
set -e
if (( rc == 0 )); then echo "FAIL AC-10: did not exit non-zero"; exit 1; fi
echo "$output" | grep -q "migration gap: file version 3 missing from schema_version while higher version applied" \
  || { echo "FAIL AC-10 message: $output"; exit 1; }
late_absent=$(psql_db "SELECT NOT EXISTS (SELECT 1 FROM information_schema.tables WHERE table_name='late');")
assert_eq "$late_absent" "t" "AC-10 base unchanged"

# --- FOUND-003: schema walking skeleton (AC-1..AC-16) -----------------------
# Reset DB then apply real migrations from repo on a clean state.
psql_db "DROP SCHEMA public CASCADE; CREATE SCHEMA public;" >/dev/null
rm -f "$SCENARIO_DIR"/*.sql
cp "$REPO_ROOT/migrations/0001_init.sql" "$SCENARIO_DIR/0001_init.sql"
cp "$REPO_ROOT/migrations/0002_schema.sql" "$SCENARIO_DIR/0002_schema.sql"

# AC-16: filename regex.
test_0002_filename_regex() {
  local name="0002_schema.sql"
  if [[ ! "$name" =~ ^[0-9]{4}_[a-z0-9_]+\.sql$ ]]; then
    echo "FAIL AC-16: filename regex"; exit 1
  fi
}
test_0002_filename_regex

# Apply migrations.
run_runner "$SCENARIO_DIR" >/dev/null

# Helper: dump column schema in canonical form (column_name|data_type|is_nullable|column_default).
dump_columns() {
  psql_db "SELECT column_name || '|' || data_type || '|' || is_nullable || '|' || COALESCE(column_default, '')
           FROM information_schema.columns
           WHERE table_name = '$1' AND table_schema = 'public'
           ORDER BY ordinal_position;"
}

assert_columns_match() {
  local table="$1" fixture="$2" ac="$3"
  local actual expected
  actual=$(dump_columns "$table")
  expected=$(awk 'NF{print}' "$REPO_ROOT/tests/migrations/fixtures/$fixture")
  if [[ "$actual" != "$expected" ]]; then
    echo "FAIL $ac columns mismatch for $table" >&2
    diff <(echo "$expected") <(echo "$actual") >&2 || true
    exit 1
  fi
}

# AC-1: users schema + tier CHECK.
assert_columns_match users expected_users_columns.txt "AC-1"
tier_check=$(psql_db "SELECT pg_get_constraintdef(c.oid)
                      FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
                      WHERE t.relname = 'users' AND c.contype = 'c';")
echo "$tier_check" | grep -q "anonymous" || { echo "FAIL AC-1 tier CHECK missing anonymous"; exit 1; }
echo "$tier_check" | grep -q "member"    || { echo "FAIL AC-1 tier CHECK missing member"; exit 1; }
echo "$tier_check" | grep -q "author"    || { echo "FAIL AC-1 tier CHECK missing author"; exit 1; }

# AC-2: sentinel users row.
sentinel=$(psql_db "SELECT id::text || '|' || tier FROM users;")
assert_eq "$sentinel" "00000000-0000-0000-0000-000000000000|anonymous" "AC-2 sentinel row"

# AC-3: documents schema + access_tier CHECK.
assert_columns_match documents expected_documents_columns.txt "AC-3"
access_check=$(psql_db "SELECT pg_get_constraintdef(c.oid)
                        FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
                        WHERE t.relname = 'documents' AND c.contype = 'c';")
echo "$access_check" | grep -q "public"      || { echo "FAIL AC-3 access_tier"; exit 1; }
echo "$access_check" | grep -q "members"     || { echo "FAIL AC-3 access_tier"; exit 1; }
echo "$access_check" | grep -q "author_only" || { echo "FAIL AC-3 access_tier"; exit 1; }

# AC-4: chunks schema + FK CASCADE + UNIQUE composite.
assert_columns_match chunks expected_chunks_columns.txt "AC-4"
fk_def=$(psql_db "SELECT pg_get_constraintdef(c.oid)
                  FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
                  WHERE t.relname = 'chunks' AND c.contype = 'f';")
echo "$fk_def" | grep -q "ON DELETE CASCADE" || { echo "FAIL AC-4 FK CASCADE missing"; exit 1; }
unique_def=$(psql_db "SELECT pg_get_constraintdef(c.oid)
                      FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
                      WHERE t.relname = 'chunks' AND c.contype = 'u';")
echo "$unique_def" | grep -qE "\(document_id, ord\)" || { echo "FAIL AC-4 UNIQUE (document_id, ord)"; exit 1; }

# AC-5: HNSW index on chunks.embedding using vector_cosine_ops.
hnsw_def=$(psql_db "SELECT indexdef FROM pg_indexes WHERE tablename='chunks' AND indexname='chunks_embedding_idx';")
echo "$hnsw_def" | grep -qi "USING hnsw"      || { echo "FAIL AC-5 hnsw method"; exit 1; }
echo "$hnsw_def" | grep -q  "vector_cosine_ops" || { echo "FAIL AC-5 cosine opclass"; exit 1; }

# AC-6: conversations schema.
assert_columns_match conversations expected_conversations_columns.txt "AC-6"

# AC-7: tickets schema + status CHECK + priority_score CHECK.
assert_columns_match tickets expected_tickets_columns.txt "AC-7"
ticket_checks=$(psql_db "SELECT pg_get_constraintdef(c.oid)
                         FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
                         WHERE t.relname = 'tickets' AND c.contype = 'c';")
echo "$ticket_checks" | grep -q "open"      || { echo "FAIL AC-7 status open"; exit 1; }
echo "$ticket_checks" | grep -q "resolved"  || { echo "FAIL AC-7 status resolved"; exit 1; }
echo "$ticket_checks" | grep -q "dismissed" || { echo "FAIL AC-7 status dismissed"; exit 1; }
echo "$ticket_checks" | grep -q "priority_score" || { echo "FAIL AC-7 priority_score CHECK"; exit 1; }

# AC-8: deleting a referenced conversation raises 23503; row stays.
psql_db "INSERT INTO conversations (id, user_id, gcs_uri)
         VALUES ('11111111-1111-1111-1111-111111111111',
                 '00000000-0000-0000-0000-000000000000',
                 'gs://bucket/conv-ac8.md');" >/dev/null
psql_db "INSERT INTO tickets (conversation_id, question)
         VALUES ('11111111-1111-1111-1111-111111111111', 'q?');" >/dev/null
delete_err=$(docker exec -i -e PGPASSWORD="$PASSWORD" "$CONTAINER" \
  psql -U postgres -d archiviste -v ON_ERROR_STOP=1 <<'SQL' 2>&1 || true
\set VERBOSITY verbose
DELETE FROM conversations WHERE id='11111111-1111-1111-1111-111111111111';
SQL
)
echo "$delete_err" | grep -q "23503" || { echo "FAIL AC-8 expected SQLSTATE 23503: $delete_err"; exit 1; }
remaining=$(psql_db "SELECT COUNT(*) FROM conversations WHERE id='11111111-1111-1111-1111-111111111111';")
assert_eq "$remaining" "1" "AC-8 conversation retained"

# AC-9: query_log schema + CHECK mode + CHECK latency_ms.
assert_columns_match query_log expected_query_log_columns.txt "AC-9"
qlog_checks=$(psql_db "SELECT pg_get_constraintdef(c.oid)
                       FROM pg_constraint c JOIN pg_class t ON t.oid = c.conrelid
                       WHERE t.relname = 'query_log' AND c.contype = 'c';")
echo "$qlog_checks" | grep -q "canon"      || { echo "FAIL AC-9 mode CHECK"; exit 1; }
echo "$qlog_checks" | grep -q "latency_ms" || { echo "FAIL AC-9 latency_ms CHECK"; exit 1; }

# AC-10: query_log indexes (user_id, created_at DESC) + (created_at).
user_idx=$(psql_db "SELECT indexdef FROM pg_indexes WHERE indexname='query_log_user_created_idx';")
echo "$user_idx" | grep -qE "user_id.*created_at DESC" \
  || { echo "FAIL AC-10 user_created_idx def: $user_idx"; exit 1; }
created_idx=$(psql_db "SELECT indexdef FROM pg_indexes WHERE indexname='query_log_created_idx';")
echo "$created_idx" | grep -qE "\(created_at\)" \
  || { echo "FAIL AC-10 created_idx def: $created_idx"; exit 1; }

# AC-11: vector dimension mismatch rejected by pgvector.
psql_db "INSERT INTO documents (id, source_path, title, content_hash)
         VALUES ('22222222-2222-2222-2222-222222222222', '/d', 't', 'h');" >/dev/null
dim_err=$(docker exec -e PGPASSWORD="$PASSWORD" "$CONTAINER" \
  psql -U postgres -d archiviste -v ON_ERROR_STOP=1 \
  -c "INSERT INTO chunks (document_id, ord, text, embedding)
      VALUES ('22222222-2222-2222-2222-222222222222', 0, 't',
              ('[' || array_to_string(array_fill(0::float, ARRAY[512]), ',') || ']')::vector);" 2>&1 || true)
echo "$dim_err" | grep -q "expected 1024 dimensions" \
  || { echo "FAIL AC-11 expected 1024 dimensions, got: $dim_err"; exit 1; }

# AC-12: tickets.status check_violation.
status_err=$(docker exec -i -e PGPASSWORD="$PASSWORD" "$CONTAINER" \
  psql -U postgres -d archiviste -v ON_ERROR_STOP=1 <<'SQL' 2>&1 || true
\set VERBOSITY verbose
INSERT INTO tickets (conversation_id, question, status)
VALUES ('11111111-1111-1111-1111-111111111111', 'q?', 'unknown');
SQL
)
echo "$status_err" | grep -q "23514" || { echo "FAIL AC-12 SQLSTATE 23514: $status_err"; exit 1; }

# AC-13: users.tier check_violation.
tier_err=$(docker exec -i -e PGPASSWORD="$PASSWORD" "$CONTAINER" \
  psql -U postgres -d archiviste -v ON_ERROR_STOP=1 <<'SQL' 2>&1 || true
\set VERBOSITY verbose
INSERT INTO users (tier) VALUES ('admin');
SQL
)
echo "$tier_err" | grep -q "23514" || { echo "FAIL AC-13 SQLSTATE 23514: $tier_err"; exit 1; }

# AC-14: schema_version row (2, 'schema walking skeleton').
sv=$(psql_db "SELECT version || '|' || description FROM schema_version WHERE version=2;")
assert_eq "$sv" "2|schema walking skeleton" "AC-14 schema_version row"

# AC-15: idempotent re-run.
output=$(run_runner "$SCENARIO_DIR")
echo "$output" | grep -q "migration 2 already applied, skipping" \
  || { echo "FAIL AC-15 idempotent log"; exit 1; }
sentinel_after=$(psql_db "SELECT COUNT(*) FROM users WHERE id='00000000-0000-0000-0000-000000000000';")
assert_eq "$sentinel_after" "1" "AC-15 sentinel count after re-run"

echo "ALL MIGRATION TESTS PASSED"
