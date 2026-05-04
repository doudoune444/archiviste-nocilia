#!/usr/bin/env bash
# Migration runner -- applies versioned SQL files in /migrations missing from schema_version.
#
# Contract (FOUND-002):
#   - File names match ^[0-9]{4}_[a-z0-9_]+\.sql$, version = first 4 digits.
#   - Each file applied in its own transaction (BEGIN ... COMMIT) followed by
#     INSERT INTO schema_version(version, description) inside the same tx.
#   - Already-applied versions are skipped with a structured log line.
#   - Gap detection: if any file version N < MAX(applied) is not in schema_version,
#     the runner exits non-zero and applies nothing.
#
# Required env: DATABASE_URL.
set -euo pipefail

MIGRATIONS_DIR="${MIGRATIONS_DIR:-/migrations}"
NAME_RE='^[0-9]{4}_[a-z0-9_]+\.sql$'

if [[ -z "${DATABASE_URL:-}" ]]; then
  echo "DATABASE_URL required" >&2
  exit 2
fi

psql_query() {
  PGOPTIONS='--client-min-messages=warning' psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -X -q -t -A "$@"
}

# Bootstrap: ensure schema_version exists before we read it. Idempotent.
psql_query -c "CREATE TABLE IF NOT EXISTS schema_version (
    version INT PRIMARY KEY,
    applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    description TEXT NOT NULL
);" >/dev/null

# Collect files + validate names.
declare -a files=()
shopt -s nullglob
for path in "$MIGRATIONS_DIR"/*.sql; do
  name=$(basename "$path")
  if [[ ! "$name" =~ $NAME_RE ]]; then
    echo "invalid migration filename: $name" >&2
    exit 3
  fi
  files+=("$path")
done
shopt -u nullglob
if (( ${#files[@]} > 0 )); then
  IFS=$'\n' read -r -d '' -a files < <(printf '%s\n' "${files[@]}" | sort && printf '\0')
fi

# Read applied versions.
applied_raw=$(psql_query -c "SELECT version FROM schema_version ORDER BY version;")
declare -A applied=()
max_applied=0
while IFS= read -r v; do
  [[ -z "$v" ]] && continue
  applied[$v]=1
  if (( v > max_applied )); then max_applied=$v; fi
done <<< "$applied_raw"

# Gap detection: any file version < max_applied that is not in applied set => fail.
for path in "${files[@]}"; do
  name=$(basename "$path")
  version=$((10#${name:0:4}))
  if (( version < max_applied )) && [[ -z "${applied[$version]:-}" ]]; then
    echo "migration gap: file version $version missing from schema_version while higher version applied" >&2
    exit 4
  fi
done

# Apply pending migrations in order, each in its own transaction.
for path in "${files[@]}"; do
  name=$(basename "$path")
  version=$((10#${name:0:4}))
  description="${name%.sql}"
  description="${description#????_}"
  description="${description//_/ }"
  description_escaped="${description//\'/\'\'}"

  if [[ -n "${applied[$version]:-}" ]]; then
    echo "migration $version already applied, skipping"
    continue
  fi

  start_ms=$(date +%s%3N)
  if ! psql "$DATABASE_URL" -v ON_ERROR_STOP=1 -X -q --single-transaction \
      -f "$path" \
      -c "INSERT INTO schema_version (version, description) VALUES ($version, '$description_escaped');" \
      >/dev/null; then
    echo "migration $version failed" >&2
    exit 5
  fi
  end_ms=$(date +%s%3N)
  echo "migration $version applied in $((end_ms - start_ms))ms"
done
