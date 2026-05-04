#!/usr/bin/env bash
# Enforce port consistency across docs and configs.
#
# Source of truth: docker-compose.yml.
# Canonical ports: gateway=8080, workers=8000, postgres=5432.
# Update CANONICAL_PORTS below if docker-compose.yml changes.
#
# Wired in:
#   - .pre-commit-config.yaml (local hook id: check-ports)
#   - .github/workflows/ci.yml (job: lint)
set -euo pipefail

CANONICAL_PORTS=(8080 8000 5432)
CANONICAL_REGEX="^($(IFS='|'; echo "${CANONICAL_PORTS[*]}"))$"

# Files to scan: docs, specs, top-level markdown, .claude rules/agents/commands, CI workflows.
# Excluded by design:
#   - docker-compose.yml (the source)
#   - gateway/, workers/ source (legitimate runtime port literals)
#   - infra/docker/*.Dockerfile (EXPOSE legitimate)
#   - lock files, generated files
SCAN_PATHS=(
  README.md
  BOOTSTRAP.md
  CLAUDE.md
  SECURITY.md
  CHANGELOG.md
  docs
  specs
  .claude
  .github/workflows
)

mapfile -t FILES < <(
  find "${SCAN_PATHS[@]}" -type f \
    \( -name '*.md' -o -name '*.yml' -o -name '*.yaml' -o -name '*.sh' \) \
    2>/dev/null
)

FAIL=0

for f in "${FILES[@]}"; do
  # Patterns that legitimately denote a network port:
  #   ://host:PORT      — URL with explicit port
  #   localhost:PORT
  #   0.0.0.0:PORT
  #   --port PORT / --port=PORT
  #   EXPOSE PORT       — Dockerfile (none scanned, but kept for safety)
  #   "PORT:PORT"       — docker-compose port mapping (compose file excluded but other yamls may use)
  matches=$(grep -nE \
    '(://[a-zA-Z0-9._-]+:[0-9]+|localhost:[0-9]+|0\.0\.0\.0:[0-9]+|--port[= ][0-9]+|\bEXPOSE[ ]+[0-9]+|"[0-9]{4,5}:[0-9]{4,5}")' \
    "$f" 2>/dev/null || true)

  [[ -z "$matches" ]] && continue

  while IFS= read -r line; do
    [[ -z "$line" ]] && continue
    line_no="${line%%:*}"
    content="${line#*:}"

    # Extract every port-like number in the matched line, but only from the matching contexts above
    while read -r port; do
      [[ -z "$port" ]] && continue
      if ! [[ "$port" =~ $CANONICAL_REGEX ]]; then
        echo "DRIFT  $f:$line_no  port=$port  (canonical: ${CANONICAL_PORTS[*]})"
        echo "       > $(echo "$content" | sed 's/^[[:space:]]*//')"
        FAIL=1
      fi
    done < <(
      echo "$content" | grep -oE '(://[a-zA-Z0-9._-]+:[0-9]+|localhost:[0-9]+|0\.0\.0\.0:[0-9]+|--port[= ][0-9]+|\bEXPOSE[ ]+[0-9]+|"[0-9]{4,5}:[0-9]{4,5}")' \
        | grep -oE '[0-9]+$'
    )
  done <<< "$matches"
done

if [[ $FAIL -eq 0 ]]; then
  echo "Ports OK — canonical: ${CANONICAL_PORTS[*]}"
fi
exit $FAIL
