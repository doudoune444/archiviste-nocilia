#!/usr/bin/env bash
# Stack integration tests -- FOUND-002 (AC-2, AC-3, AC-6).
#
# AC-2: Redis rejects connection without password (no `-a`).
# AC-3: Redis key written before `docker compose restart redis` is still readable.
# AC-6: `docker compose up -d` (no profile) does not start `migrator`;
#       `docker compose --profile tools config --services` includes `migrator`.
#
# Requires: docker daemon, repo built (`docker compose build` already run).
set -euo pipefail

cd "$(dirname "$0")/../.."

REDIS_PASSWORD="${REDIS_PASSWORD:-stack-test-pass}"
export REDIS_PASSWORD

# Ensure compose stack is reset between scenarios.
cleanup() {
  docker compose down -v --remove-orphans >/dev/null 2>&1 || true
}
trap cleanup EXIT

cleanup

# AC-6: services declared without `--profile tools` MUST exclude `migrator`.
default_services=$(docker compose config --services | sort)
if grep -qx "migrator" <<<"$default_services"; then
  echo "FAIL AC-6: migrator should not appear in default services" >&2
  exit 1
fi
tools_services=$(docker compose --profile tools config --services | sort)
if ! grep -qx "migrator" <<<"$tools_services"; then
  echo "FAIL AC-6: migrator missing under --profile tools" >&2
  exit 1
fi

# Bring up redis only -- enough for AC-2 / AC-3 and faster than full stack.
docker compose up -d redis >/dev/null

# Wait for redis to become healthy.
for _ in $(seq 1 30); do
  status=$(docker compose ps --format json redis 2>/dev/null \
    | python -c "import json,sys; raw=sys.stdin.read().strip();
data=json.loads(raw) if raw.startswith('[') else [json.loads(l) for l in raw.splitlines() if l]
print((data[0].get('Health') or data[0].get('State') or '') if data else '')" 2>/dev/null || echo "")
  if [[ "$status" == "healthy" ]]; then break; fi
  sleep 1
done
if [[ "$status" != "healthy" ]]; then
  echo "FAIL: redis never became healthy (last status: $status)" >&2
  docker compose logs redis >&2 || true
  exit 1
fi

# Confirm runtime: `up -d redis` did not implicitly start migrator (AC-6 runtime check).
if docker compose ps --services --filter status=running | grep -qx "migrator"; then
  echo "FAIL AC-6: migrator is running after plain `up -d`" >&2
  exit 1
fi

# AC-2: connection without `-a` must be rejected.
no_auth_output=$(docker compose exec -T redis redis-cli PING 2>&1 || true)
if ! grep -qi "NOAUTH" <<<"$no_auth_output"; then
  echo "FAIL AC-2: expected NOAUTH rejection, got: $no_auth_output" >&2
  exit 1
fi

# AC-2: connection with correct password must succeed.
auth_output=$(docker compose exec -T redis \
  redis-cli --no-auth-warning -a "$REDIS_PASSWORD" PING 2>&1 || true)
if ! grep -qx "PONG" <<<"$auth_output"; then
  echo "FAIL AC-2: expected PONG with -a, got: $auth_output" >&2
  exit 1
fi

# AC-3: write a key, restart redis, key must survive (volume + AOF).
sentinel_value="ac3-$(date +%s)"
docker compose exec -T redis \
  redis-cli --no-auth-warning -a "$REDIS_PASSWORD" \
  SET ac3:probe "$sentinel_value" >/dev/null

docker compose restart redis >/dev/null

# Wait for redis to recover post-restart.
for _ in $(seq 1 30); do
  if docker compose exec -T redis \
       redis-cli --no-auth-warning -a "$REDIS_PASSWORD" PING 2>/dev/null \
       | grep -qx "PONG"; then
    break
  fi
  sleep 1
done

retrieved=$(docker compose exec -T redis \
  redis-cli --no-auth-warning -a "$REDIS_PASSWORD" \
  GET ac3:probe 2>/dev/null | tr -d '\r')
if [[ "$retrieved" != "$sentinel_value" ]]; then
  echo "FAIL AC-3: expected [$sentinel_value], got [$retrieved]" >&2
  exit 1
fi

echo "ALL STACK TESTS PASSED"
