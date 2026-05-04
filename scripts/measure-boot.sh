#!/usr/bin/env bash
# Measure local stack boot SLA -- FOUND-002 (AC-11, AC-12, AC-13).
#
# 1. Verify the 4 service images exist locally before `up -d` (AC-11).
# 2. Run `docker compose up -d`, poll `docker compose ps --format json` until
#    all services are `healthy`, recording per-service healthy_at_seconds.
# 3. Emit a JSON artefact at $BOOT_METRICS_OUT (default boot-metrics.json)
#    with shape { total_seconds, sla_seconds, passed, services[] }.
# 4. Exit 0 regardless of pass/fail (mesure non-bloquante, AC-13);
#    image-missing case exits non-zero (AC-11).
set -euo pipefail

SLA_SECONDS="${SLA_SECONDS:-30}"
OUT_PATH="${BOOT_METRICS_OUT:-boot-metrics.json}"
TIMEOUT_SECONDS="${BOOT_TIMEOUT_SECONDS:-180}"
SERVICES=(postgres redis workers gateway)

PROJECT="${COMPOSE_PROJECT_NAME:-$(basename "$(pwd)" | tr '[:upper:]' '[:lower:]' | tr -cd 'a-z0-9_-')}"

resolve_image() {
  # Returns the image name declared for a service, or empty if it is built.
  docker compose config --format json 2>/dev/null \
    | python -c "import json,sys; svc=json.load(sys.stdin)['services'].get(sys.argv[1],{}); print(svc.get('image',''))" "$1" \
    || true
}

# AC-11: image presence check before `up -d`.
for svc in "${SERVICES[@]}"; do
  explicit=$(resolve_image "$svc")
  candidates=()
  [[ -n "$explicit" ]] && candidates+=("$explicit")
  candidates+=("${PROJECT}-${svc}" "${PROJECT}_${svc}")
  found=""
  for img in "${candidates[@]}"; do
    if docker image inspect "$img" >/dev/null 2>&1; then
      found="$img"; break
    fi
  done
  if [[ -z "$found" ]]; then
    name="${explicit:-${PROJECT}-${svc}}"
    echo "Image $name missing. Run 'docker compose build' first." >&2
    exit 1
  fi
done

start_epoch=$(date +%s.%N)
docker compose up -d "${SERVICES[@]}" >/dev/null

declare -A healthy_at
deadline=$(awk "BEGIN{print $start_epoch + $TIMEOUT_SECONDS}")

while :; do
  now=$(date +%s.%N)
  if awk "BEGIN{exit !($now > $deadline)}"; then break; fi

  # `docker compose ps --format json` emits NDJSON on compose v2.0..v2.20 and a
  # single JSON array on v2.21+. Parse the whole buffer once with a tolerant
  # reader so neither form silently degrades to "no service ever healthy".
  ps_buffer=$(docker compose ps --format json 2>/dev/null || true)
  while IFS=$'\t' read -r svc state; do
    [[ -z "$svc" ]] && continue
    if [[ "$state" == "healthy" && -z "${healthy_at[$svc]:-}" ]]; then
      healthy_at[$svc]=$(awk "BEGIN{printf \"%.2f\", $now - $start_epoch}")
    fi
  done < <(printf '%s' "$ps_buffer" | python -c "
import json, sys
raw = sys.stdin.read().strip()
if not raw:
    sys.exit(0)
try:
    parsed = json.loads(raw)
    items = parsed if isinstance(parsed, list) else [parsed]
except json.JSONDecodeError:
    items = []
    for line in raw.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            items.append(json.loads(line))
        except json.JSONDecodeError:
            continue
for item in items:
    name = item.get('Service', '')
    health = item.get('Health') or item.get('State', '')
    print(f'{name}\t{health}')
")

  all_done=1
  for svc in "${SERVICES[@]}"; do
    [[ -z "${healthy_at[$svc]:-}" ]] && all_done=0
  done
  (( all_done == 1 )) && break
  sleep 1
done

end_epoch=$(date +%s.%N)
total=$(awk "BEGIN{printf \"%.2f\", $end_epoch - $start_epoch}")

# Serialize JSON artefact (AC-12). Services that never reached healthy
# get healthy_at_seconds = -1 so the schema stays stable.
SERVICES_CSV=$(IFS=,; echo "${SERVICES[*]}")
HEALTHY_CSV=""
for svc in "${SERVICES[@]}"; do
  HEALTHY_CSV+="${svc}=${healthy_at[$svc]:--1};"
done

SERVICES_CSV="$SERVICES_CSV" HEALTHY_CSV="$HEALTHY_CSV" \
TOTAL="$total" SLA="$SLA_SECONDS" OUT="$OUT_PATH" \
python - <<'PY'
import json, os
total = float(os.environ["TOTAL"])
sla = float(os.environ["SLA"])
names = os.environ["SERVICES_CSV"].split(",")
hmap = {}
for kv in os.environ["HEALTHY_CSV"].split(";"):
    if not kv: continue
    k, v = kv.split("=", 1)
    hmap[k] = float(v)
artefact = {
    "total_seconds": total,
    "sla_seconds": sla,
    "passed": total <= sla and all(hmap.get(n, -1) >= 0 for n in names),
    "services": [{"name": n, "healthy_at_seconds": hmap.get(n, -1.0)} for n in names],
}
with open(os.environ["OUT"], "w", encoding="utf-8") as f:
    json.dump(artefact, f, indent=2)
print(json.dumps(artefact))
PY

exit 0
