#!/usr/bin/env bash
# Integration tests for migrations/run.sh -- covers AC-7, AC-8, AC-9, AC-10.
#
# Spins up a disposable postgres:16 container, exercises four scenarios:
#   1. fresh DB applies all files (AC-7).
#   2. re-run logs `already applied, skipping`, exit 0 (AC-9).
#   3. failing SQL on version N rolls back N only, prior versions intact (AC-8).
#   4. file version < MAX(applied) but absent from schema_version => gap exit (AC-10).
#
# Requires: docker. Uses postgres:16 image for both server and psql client.
set -euo pipefail

cd "$(dirname "$0")"

CONTAINER="archiviste-mig-test-$$"
NETWORK="archiviste-mig-net-$$"
PASSWORD="testpass"

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
  postgres:16 >/dev/null

for _ in $(seq 1 30); do
  if docker exec "$CONTAINER" pg_isready -U postgres >/dev/null 2>&1; then break; fi
  sleep 1
done

run_runner() {
  docker run --rm --network "$NETWORK" \
    -e DATABASE_URL="postgres://postgres:${PASSWORD}@${CONTAINER}:5432/archiviste" \
    -v "$1:/migrations:ro" \
    --entrypoint /bin/bash postgres:16 /migrations/run.sh
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

echo "ALL MIGRATION TESTS PASSED"
