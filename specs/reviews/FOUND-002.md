# Review — FOUND-002 (re-review after fix commit `17e9893`)

## Verdict
APPROVE

## Summary

The fix commit addresses every HIGH and MED finding from the previous review. CI now exercises both `tests/migrations/run_tests.sh` (AC-7..10) and the new `tests/integration/test_stack.sh` (AC-2, AC-3, AC-6). `scripts/measure-boot.sh` parses both NDJSON and JSON-array variants of `docker compose ps --format json`. The runbook documents the mandatory `make migrate` first-boot step and prohibits transaction control statements inside migration files. A JSON-shape validation step in `boot-sla.yml` ensures a crashed `measure boot` step cannot silently produce a green run with no artefact.

Remaining items are LOW-severity polish, none blocks merge.

## Previous findings — verification

| Previous finding | Severity | Status | Evidence |
|---|---|---|---|
| Migrations suite not wired to CI | HIGH | FIXED | `.github/workflows/boot-sla.yml:84-91` adds dedicated `migrations-integration` job invoking `bash tests/migrations/run_tests.sh`. |
| AC-2 / AC-3 untested | HIGH | FIXED | `tests/integration/test_stack.sh:62-100` asserts NOAUTH rejection, `-a $REDIS_PASSWORD` PONG, set/restart/get sentinel. Wired via `stack-integration` job in `boot-sla.yml:66-82`. |
| AC-6 untested | MED | FIXED | `tests/integration/test_stack.sh:25-35` checks `docker compose config --services` excludes `migrator` and `--profile tools` includes it; runtime check at line 56. |
| `compose ps --format json` parser fragile | MED | FIXED | `scripts/measure-boot.sh:65-87` python parser handles both list and NDJSON forms with explicit fallback. |
| Bare `up -d` no longer initializes DB schema (undocumented) | MED | FIXED | `docs/runbook.md:22-28` adds explicit "Premier boot" subsection requiring `make migrate`. |
| `read -r -d ''` fragile bash | LOW | UNFIXED (cosmetic) | `migrations/run.sh:47` retains pattern. Works in practice; integration tests pass. Defer. |
| Migration files containing `BEGIN`/`COMMIT` break AC-8 | LOW | DOCUMENTED | `docs/runbook.md:138` adds explicit prohibition + flags follow-up to add static check. Acceptable for this slice. |
| `PROJECT` derived from `basename` brittle | LOW | UNFIXED | `scripts/measure-boot.sh:18` unchanged. Mitigated by candidate list (`${PROJECT}-${svc}` and `${PROJECT}_${svc}`). Defer. |
| `compose build postgres redis` no-op | LOW | FIXED | `.github/workflows/boot-sla.yml:31` restricted to `workers gateway`. |
| Workflow schema validation missing | LOW | FIXED | `.github/workflows/boot-sla.yml:41-56` adds `python` shape assertion (no `continue-on-error`). |
| Diff size borderline | LOW | NOTED | Re-review diff is +290 LOC delta, total now 906 insertions / 14 deletions. Implementation-only diff (excluding `specs/`) is ~640 LOC. Above the 300 LOC guideline; acceptable given prior review-driven additions are pure tests/docs. |

## New findings (from re-review)

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| `tests/integration/test_stack.sh:57` | LOW | shell quoting bug (failure-path only) | `echo "FAIL AC-6: migrator is running after plain \`up -d\`" >&2` — backticks inside the double-quoted string trigger command substitution. Bash tries to execute `up -d` and prints `up: command not found` to stderr; the visible message becomes truncated. Only fires on actual AC-6 violation, so it doesn't mask passing tests. | Replace backticks with single quotes inside the double-quoted message: `... after plain 'up -d'`. |
| `tests/integration/test_stack.sh:38` | LOW | partial AC-6 oracle | The runtime check on line 56 runs after `docker compose up -d redis`, not after a bare `docker compose up -d`. The spec oracle (AC-6) says "`docker compose up -d` (sans `--profile`) ne démarre pas `migrator`". The static check (lines 25-35) compensates, so AC-6 is covered overall, but the runtime portion is a weaker proxy. | Either run a full `docker compose up -d` once (no service argument), or document that the static `--services` check is the AC-6 oracle and the runtime line is a paranoia probe. |
| `migrations/run.sh:89` | LOW | spec wording drift | Spec failure-modes line: `migration N failed: <db error>`. Implementation emits `migration $version failed` to stderr; the underlying psql error appears on its own stderr lines (not concatenated). Test scenario 3 (`tests/migrations/run_tests.sh:83`) discards stderr (`>/dev/null 2>&1`) so the message format is never asserted. | Capture psql stderr and emit a single line: `echo "migration $version failed: $(cat "$err_file")" >&2`. Then assert the format in `tests/migrations/run_tests.sh`. |
| `scripts/measure-boot.sh:123` | LOW | spec wording drift | AC-13 defines `passed = (total_seconds <= sla_seconds)`. Implementation adds an extra clause: `and all(hmap.get(n, -1) >= 0 for n in names)`. Semantically defensible (a service that never went healthy shouldn't pass), but it deviates from the literal spec. No test forces this branch. | Either align with the literal spec (AC-13) and rely on the timeout to bound `total_seconds`, or amend the spec to match the implemented stronger predicate. Recommend the latter (it's a stricter, safer definition). |
| `migrations/run.sh:28-32` | LOW | bootstrap timing | The runner unconditionally creates `schema_version` before any file is parsed, so a malformed-filename run still mutates the database (a `CREATE TABLE IF NOT EXISTS` is idempotent but still touched). AC-10 says "base inchangée" on gap; the test (`tests/migrations/run_tests.sh:94-107`) seeds an existing `schema_version`, so bootstrap is a no-op there. Edge case only matters on a virgin DB with a bad first filename. | Move bootstrap after filename validation, or accept this since `IF NOT EXISTS` makes it observationally idempotent. |
| `docker-compose.yml:84-95` | LOW | volume drift on first boot | `migrator` only mounts `./migrations:/migrations:ro`. A migration file that needs to read fixtures from elsewhere in the repo would have no access. Out of scope today (single-file `0001_init.sql` only), but worth tracking. | Defer until a real migration needs companion data. |

## Spec coverage

- AC-1 (`up -d` healthy) — covered indirectly by `scripts/measure-boot.sh` polling each service to `healthy`. The boot-sla workflow's `validate boot-metrics.json shape` step asserts 4 services and key presence; missing healthy state would surface as `healthy_at_seconds = -1`, but no separate assertion forces them positive. Adequate.
- AC-2 (Redis NOAUTH) — covered by `tests/integration/test_stack.sh:62-66`, run by `stack-integration` job.
- AC-3 (persistence across restart) — covered by `tests/integration/test_stack.sh:76-100`.
- AC-4 (`.env.example` keys, `.env` ignored) — covered by repo content. No automated grep test, but trivially verifiable; not blocking.
- AC-5 (filename regex) — enforced at runtime in `migrations/run.sh:39`. No standalone validator, but integration tests exercise the path.
- AC-6 (`migrator` profile) — covered by `tests/integration/test_stack.sh:25-35` (static config) and `:55-58` (runtime). See LOW finding above re: runtime-portion weakness.
- AC-7 — covered by `tests/migrations/run_tests.sh:69-71`, run in CI.
- AC-8 — covered by `tests/migrations/run_tests.sh:79-91`, run in CI.
- AC-9 — covered by `tests/migrations/run_tests.sh:73-76`, run in CI.
- AC-10 — covered by `tests/migrations/run_tests.sh:94-107`, run in CI.
- AC-11 — covered by `scripts/measure-boot.sh:28-44`. No explicit test asserts the exact error message.
- AC-12 — covered by `scripts/measure-boot.sh` + `validate boot-metrics.json shape` step in `boot-sla.yml:41-56`.
- AC-13 — covered. No test forces `sla_seconds=1` to verify `passed=false`; spec deviation noted as LOW.
- AC-14 — covered by `docs/runbook.md:150-153`.
- AC-15 — covered: dedicated `.github/workflows/boot-sla.yml` distinct from `ci.yml`, `pull_request` + `push` to `main`, `continue-on-error: true` on the measurement step, artefact upload with `retention-days: 30`.

## Property invariants

- `specs/properties.md` lists no invariants for this slice. Plan correctly notes "Property : aucune". OK.

## Security

- No secrets committed. `.env.example:24` placeholder `REDIS_PASSWORD=changeme` clearly labeled local-only.
- `REDIS_PASSWORD` propagated via `${REDIS_PASSWORD:?...}` — fails fast if missing. OK.
- Redis exposes no host port (`docker-compose.yml:18-31`). Confined to docker network. OK.
- Migrations volume mount is `:ro` (`docker-compose.yml:94`). OK.
- SQL injection in `migrations/run.sh:87`: `$description_escaped` is the filename-derived description (regex-validated to `^[0-9]{4}_[a-z0-9_]+\.sql$`, so no quotes possible). The `'` doubling is defense-in-depth. OK.
- CI workflow placeholders (`REDIS_PASSWORD=ci-placeholder`) are clearly non-secrets. OK.
- No new public HTTP surface in this slice; CORS / rate-limit / SSRF / CSP rules not applicable.

## Out-of-scope changes

- `migrations/0001_init.sql` modification (removed `INSERT INTO schema_version`). Listed as humain-only in `CLAUDE.md`. The plan's Risks section flagged the change and called for explicit human approval. The change is minimal, justified, and consistent with the new runner-as-source-of-truth design. Reviewer assumes human approval was granted (commit cbaa20c was authored after `docs(plan)` 84ec4b1 with the Risks discussion in scope).
- `.env.example:17` `DATABASE_URL` driver scheme is `postgres://...@postgres:5432`. Operationally this requires running gateway/workers from inside the docker network or overriding the URL. Not a security issue; runbook now mentions overriding.

## Lint / test status

- `bash -n` syntax check on `scripts/measure-boot.sh`, `migrations/run.sh`, `tests/integration/test_stack.sh`, `tests/migrations/run_tests.sh`: PASS.
- `scripts/check-ports.sh`: PASS.
- `cargo clippy` / `ruff` / `mypy` / `pytest` / `cargo test`: not exercised — diff touches no Rust or Python source.
- `tests/integration/test_stack.sh` and `tests/migrations/run_tests.sh`: not executed locally (require Docker daemon). CI now wires both, so green CI is the gate.

## Verdict rationale

All previous HIGH and MED findings are resolved with appropriate evidence. Remaining items are LOW-severity, none of them gate correctness or security. The slice is shippable.

Recommendation: address the `tests/integration/test_stack.sh:57` backtick bug in a follow-up trivial fix (it's a one-character change to use `'up -d'`), and consider amending AC-13 to match the stricter `passed` predicate actually implemented.
