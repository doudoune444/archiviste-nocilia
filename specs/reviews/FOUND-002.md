# Review — FOUND-002

## Verdict
REQUEST_CHANGES

## Findings

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| `.github/workflows/boot-sla.yml`, `.github/workflows/ci.yml` (absent reference) | HIGH | spec violation — oracle not enforced | `tests/migrations/run_tests.sh` is committed but not invoked by any workflow. AC-7/8/9/10 oracles never execute in CI. Plan explicitly listed this suite under "Test strategy". | Add a `migrations` job in `ci.yml` (or a dedicated workflow) that runs `bash tests/migrations/run_tests.sh` on a runner with Docker. |
| (absent file) | HIGH | spec violation — AC-2 / AC-3 untested | No script anywhere asserts (a) `redis-cli PING` (no `-a`) returns `NOAUTH`, (b) a key written before `docker compose restart redis` is still readable. Plan promised "scénario set/restart/get sur clé Redis". | Add `tests/integration/test_stack.sh` exercising the two scenarios; wire to CI. |
| (absent file) | MED | spec violation — AC-6 untested | No automated check that `docker compose up -d` (without `--profile tools`) leaves `migrator` not running. | Trivial bash assertion: `docker compose ps --services --filter status=running | grep -vq '^migrator$'`. |
| `scripts/measure-boot.sh:57-64` | MED | quality / format drift | Parses `docker compose ps --format json` line-by-line. Compose v2.21+ emits a single JSON **array** (not NDJSON) — the per-line `python -c "json.loads"` raises and is silenced by `\|\| true`, leaving `healthy_at` empty for every service and `passed=false` even on a fast boot. Pin to `--format json` works only on older compose. | Use `docker compose ps --format json \| jq -c '.[]?, .'` style with detection, or use `--format '{{json .}}'` (Go template) which always emits one object per line. |
| `docker-compose.yml:11-12` | MED | behavioral regression undocumented | Removing the `./migrations:/docker-entrypoint-initdb.d:ro` mount means a fresh `docker compose up -d` no longer creates `vector` / `pgcrypto` extensions or `schema_version`. AC-1 still passes (postgres healthcheck = `pg_isready`). User must now remember `make migrate`. Runbook update mentions the migration command but does not call out that bare `up -d` no longer initializes the DB schema. | Add an explicit "First boot" subsection to `docs/runbook.md` ("after `docker compose up -d`, run `make migrate`"). Optionally make `gateway`/`workers` depend on `migrator` via a separate non-tools service or a `condition: service_completed_successfully`. |
| `migrations/run.sh:47` | LOW | fragile bash | `IFS=$'\n' read -r -d '' -a files < <(printf '%s\n' "${files[@]}" \| sort && printf '\0')` mixes `read -d ''` (NUL delimiter) with newline-delimited input. Works by accident because `read` reads until NUL and IFS splits the buffer; under `set -e` and unusual filenames this is brittle. | `mapfile -t files < <(printf '%s\n' "${files[@]}" \| sort)` is simpler and equivalent for this use case. |
| `migrations/run.sh:85-91` | LOW | undocumented invariant | Runner uses `psql --single-transaction -f file -c "INSERT ..."`. If a migration file contains its own `BEGIN`/`COMMIT`, the inner COMMIT closes the wrapping transaction early and the INSERT runs outside it. AC-8 contract assumes one transaction per file. | Either document "migration files MUST NOT contain transaction control statements" in `docs/runbook.md` and reject offending files, or wrap with explicit `BEGIN; \i file; INSERT ...; COMMIT;` and parse-reject `BEGIN`/`COMMIT` tokens. |
| `scripts/measure-boot.sh:18` | LOW | hidden coupling | `PROJECT` is derived from `basename "$(pwd)"`. If the repo is cloned to a directory other than `archiviste-nocilia`, `docker image inspect` lookups for built images (`<project>-gateway`, `<project>-workers`) will miss and the script exits with a misleading "Image missing" error even though images exist. | Read the project name from `docker compose config --format json \| jq -r '.name'` instead. |
| `.github/workflows/boot-sla.yml:30` | LOW | quality | `docker compose build postgres redis workers gateway` invokes build on `postgres` and `redis` which only declare `image:`. Compose treats this as a no-op but emits a warning and wastes CI time. | Restrict to `docker compose build workers gateway`. |
| `.github/workflows/boot-sla.yml` (entire file) | LOW | observability | Workflow uses `continue-on-error: true` on the measurement step + `if: always()` on upload, so a crashed script silently produces no artefact and the workflow stays green. No assertion that `boot-metrics.json` was even created or contains 4 service entries. | Add a final `jq` step (without continue-on-error) validating the JSON shape (presence of `total_seconds`, `services` array length 4). Spec only forbids gating on `passed`, not on schema correctness. |
| Diff size | LOW | vertical-slice borderline | Total diff 661 LOC. Excluding `specs/` (200 LOC) and migrations SQL (6 LOC), implementation diff is ~455 LOC vs the ≤300 LOC rule. Acceptable given multiple disjoint concerns (redis + migrations + boot SLA), but at the edge. | Future tickets should split redis-only / migrations / boot-SLA into separate slices. |

## Spec coverage

- AC-1 (`docker compose up -d` healthy) — partial. Manual via `scripts/measure-boot.sh`, no automated assertion in CI beyond boot-sla measurement (which is non-blocking). No test in `tests/integration`.
- AC-2 (Redis auth required) — NOT covered. No test asserts the no-`-a` rejection.
- AC-3 (Redis persistence across restart) — NOT covered. No test exercises restart + key survival.
- AC-4 (`.env.example` keys, `.env` ignored) — covered by repo content; no automated grep test.
- AC-5 (filename regex) — runner enforces at runtime via `NAME_RE` (`migrations/run.sh:16`). No standalone validator test.
- AC-6 (`migrator` under `profiles: ["tools"]`) — covered in `docker-compose.yml:87`; no automated test that `up -d` excludes it.
- AC-7 (fresh apply) — covered by `tests/migrations/run_tests.sh:69-71` BUT not run in CI.
- AC-8 (per-file rollback) — covered by `tests/migrations/run_tests.sh:79-91` BUT not run in CI.
- AC-9 (skip already-applied) — covered by `tests/migrations/run_tests.sh:73-76` BUT not run in CI.
- AC-10 (gap detection) — covered by `tests/migrations/run_tests.sh:94-107` BUT not run in CI.
- AC-11 (image presence pre-check) — covered by `scripts/measure-boot.sh:28-44`. No standalone test asserts the exact error message.
- AC-12 (JSON artefact shape) — covered by `scripts/measure-boot.sh:85-106`. No JSON-schema validation.
- AC-13 (`passed` flag, exit 0) — covered by `scripts/measure-boot.sh:100, 108`. No test forces `sla_seconds=1` to verify `passed=false` path.
- AC-14 (runbook baselines) — covered: `docs/runbook.md` mentions both "Dev local : 4 cœurs / 8 GiB RAM / SSD" and "CI : `ubuntu-latest`".
- AC-15 (dedicated workflow, retention 30, continue-on-error) — covered by `.github/workflows/boot-sla.yml`.

Net: AC-2, AC-3, AC-6 have zero automated test. AC-7..10 tests are committed but not run.

## Property invariants

- `specs/properties.md` lists no invariants relevant to this slice. Plan correctly notes "Property : aucune". OK.

## Security

- No secrets committed. `.env.example` placeholder `REDIS_PASSWORD=changeme` clearly labeled as local-only (`.env.example:18-20`). OK per `.claude/rules/secret-hygiene.md`.
- `REDIS_PASSWORD` propagated via env interpolation `${REDIS_PASSWORD:?...}` — fails fast if missing. OK.
- Redis exposes no host port (`docker-compose.yml:18-31`). OK.
- `migrations/:/migrations:ro` mount read-only (`docker-compose.yml:94`). OK.
- DATABASE_URL in `.env.example` no longer routes through `localhost` — it now points to `postgres://postgres:postgres@postgres:5432/archiviste`, which only works inside the docker network. Local `cargo run` of the gateway from the host (referenced in `CLAUDE.md`) will now fail to connect unless the user overrides `DATABASE_URL`. Operational papercut, not a security finding.
- SQL injection vector in `migrations/run.sh:87`: `description_escaped` string-concatenated into the INSERT. Single-quote doubling is correct, no other quoting required, and the source is a controlled filename matching `^[0-9]{4}_[a-z0-9_]+\.sql$` (no quotes). OK in practice but cite-worthy.
- No CORS / rate-limit / SSRF surface in this diff (infra only).

## Out-of-scope changes

- `migrations/0001_init.sql` — listed under `migrations/*.sql` (humain-only) in `CLAUDE.md`. Plan flagged the change and requested explicit approval. Modification is minimal (removed duplicate `INSERT INTO schema_version`), justified by the plan's Risks section. Reviewer flags but does not block — verify human approval was granted before merge.
- `.env.example:17` — `DATABASE_URL` driver scheme changed from `postgresql+asyncpg://...localhost:5432` to `postgres://...@postgres:5432`. Affects any host-side SQLAlchemy/asyncpg connection. Plan's "Files to touch" mentions adding `REDIS_PASSWORD`/`DATABASE_URL` keys but not changing the driver/host. Justify or revert; if intended, add a comment explaining host-network vs. docker-network usage.

## Lint / test status

- `bash -n` syntax check on all three new shell scripts: PASS.
- `scripts/check-ports.sh`: PASS (no drift detected).
- `cargo clippy` / `ruff` / `mypy` / `pytest` / `cargo test`: not exercised — diff touches no Rust or Python source. No language-level lint regressions.
- `tests/migrations/run_tests.sh`: not executed locally (requires Docker daemon); script is syntactically sound. **Must be wired to CI before merge** (see HIGH finding).
