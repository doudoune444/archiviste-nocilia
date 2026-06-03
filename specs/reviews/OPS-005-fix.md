# Review — OPS-005-fix (branch `fix/OPS-005-job-clone-repo`, commit `8dd0916`)

## Verdict
REQUEST_CHANGES

The diff is clean, secure, in-scope, and terraform-green. But it does NOT make the
Job succeed at runtime: a FOURTH defect the RCA missed (ingest CLI never wires Cloud
SQL IAM token auth) means the Job, after A/B/C are fixed, now reaches `create_pool`
and fails DB auth → exit 2 → execution `Failed`. The original `ModuleNotFoundError`
(exit 1, ~3s) masked this downstream failure. Fixing the symptom moves the failure,
it does not close the loop.

## Findings

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| workers/src/archiviste_workers/ingest/cli.py:115 | HIGH | incomplete-fix / Job still fails | `pool = await create_pool(settings.database_url)` — no `token_provider`. Job sets `CLOUD_SQL_IAM_AUTH=true` (cloud_run_job.tf:73-76) and `DATABASE_URL` has NO password (cloud_run_job.tf:83). IAM DB login in this codebase = OAuth bearer passed as asyncpg `password` (see `main.py:60-62` for the service; `token.py` whole file). CLI skips it → asyncpg auth fails → caught at `cli.py:116` → `EXIT_INIT_FAILURE` (2) → execution Failed → red. The Job will NOT succeed after this fix. | CLI must mirror `main.py:60-62`: `SqlTokenProvider() if settings.cloud_sql_iam_auth else None` passed into `create_pool(...)`. This is the real defect-D. Either widen OPS-005-fix scope to patch cli.py, or open a follow-up ING ticket and mark OPS-005-fix as "not yet green at runtime" rather than RESOLVED. |
| docs/blockers.md:189 | MED | premature "RESOLVED" | Status line: `RESOLVED (pending merge + redeploy + re-test)`. The fix is unverified end-to-end and (per HIGH above) will still fail at DB auth. Calling it RESOLVED is optimistic. | Downgrade to `IN PROGRESS` / `PARTIAL` until a real execution returns `Succeeded`; add defect-D to the entry. |
| infra/terraform/variables.tf:15 | LOW | config-dependent silent break | `github_repo` default `doudoune444/archiviste-nocilia`. The runtime clone URL (cloud_run_job.tf:55) is `https://github.com/${var.github_repo}.git`. If the real public repo owner differs from whatever value is applied, clone 404s → exit non-zero → red (loud, not silent — acceptable). Clone + WIF share the same var so they cannot diverge. | Confirm the applied `github_repo` matches the actual public repo owner before redeploy. No code change required. |
| .github/workflows/deploy.yml:62 | LOW | tag/cache interaction | `:latest` and `:sha` are pushed from the same `build-push-action` step with `cache-to: gha,mode=max`. Both tags point at the same digest — correct. No effect on canary (`:sha`, l.85), promote (revision name, l.149), or rollback (`revisions list`, l.224). Additive only. | None. Noted for completeness. |

## Verification performed

| Check | Result |
|---|---|
| `terraform fmt -check -recursive` (infra/terraform) | PASS (exit 0) |
| `terraform init -backend=false` | PASS (exit 0) |
| `terraform validate` | PASS — "The configuration is valid." |
| `deploy.yml` YAML parse + multiline `tags:` | PASS — block scalar → two newline-separated tags, exactly `docker/build-push-action` format |
| Scope (files changed) | 6 files, +46/-8, < 300 LOC. Matches RCA-stated set. No `specs/`, no Rust/Python SOURCE touched → cargo/pytest/mypy/ruff N/A for this diff |
| `lore/` present in repo root | YES (`lore/sample/*.md`) — clone will contain corpus |

## Reasoning on the asked points

- **Exit-code preservation (AC-7):** CORRECT. `set -e; git clone ... && cd ... && exec python` — `&&` short-circuits so clone/cd failure → shell exits non-zero → execution Failed (clone failure → red, as intended). `exec` replaces the shell with python, so ING-001's 0/1/2 becomes the container exit code verbatim. If `exec` itself fails (missing interpreter), `set -e` exits non-zero → Failed (no false green). `set -e` is mostly redundant under the `&&` chain but harmless.
- **`/app/.venv/bin/python` exists?** PLAUSIBLE. `WORKDIR /app` + `uv sync` (Dockerfile:9-12) creates `/app/.venv` by default; the service CMD uses `uv run` which activates the same venv. No `UV_PROJECT_ENVIRONMENT` override found. Assumption is reasonable but unverified against a built image. `exec /app/.venv/bin/python` failing surfaces loudly (non-zero), so the risk is detectable, not silent. `uv run -m archiviste_workers.ingest` would be more robust (resolves the venv regardless of path), but the explicit path is acceptable.
- **find_repo_root + `--depth 1`:** CORRECT. A shallow clone still writes a real `.git/` dir; `cli.py:58-64` walks up from `cwd=/srv/repo` and finds `/srv/repo/.git` immediately. `resolve_target("lore/", /srv/repo)` resolves under root; `lore/` exists in the clone. No `--depth 1` interaction with `.git` detection.
- **Security (sh -c):** NO violation. The shell string contains only the static `var.github_repo` (Terraform-controlled), no user input → does NOT trigger security.md "shell-mode subprocess with user input". SSRF A10: fixed trusted HTTPS URL (own public repo), anonymous clone, no credentials, no user-supplied host. Secret hygiene: nothing leaked; `MISTRAL_API_KEY` stays `secret_key_ref`; `DATABASE_URL` carries no password. Container runs as root (no `USER` in Dockerfile) so `/srv/repo` is writable.
- **`:latest` / canary flow:** YAML valid; canary/promote/rollback all still key off `:sha` / revision names → no regression.
- **Remaining runtime failure the fix misses:** YES — the HIGH finding above (cli.py never wires `SqlTokenProvider` for IAM auth). This is the decisive gap.

## Spec coverage (against OPS-005 ACs — fix-relevant subset)

- AC-2 (`--path lore/`, Cloud SQL IAM env): command/env preserved ✓; but IAM auth not actually exercisable by the CLI (HIGH).
- AC-6/AC-7 (exit-code → execution-state map): mechanism (`exec`) correct ✓; cannot be reached green until DB-auth defect-D fixed.
- AC-1/AC-3/AC-4/AC-5/AC-8/AC-9/AC-10: unchanged by this fix (covered by original OPS-005 review).

## Out-of-scope changes
None. All 6 files are the RCA-declared set; no unrelated drift; no `specs/` mutation.

## Bottom line
Approve the *approach* and the diff quality. Do NOT merge as "resolved": add the
`SqlTokenProvider` wiring to `cli.py` (or scope a follow-up ticket) so the Job
actually reaches `Succeeded`, and downgrade the blockers.md status until a real
execution confirms green.
