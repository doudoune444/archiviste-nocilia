# Review — OPS-005-fix (branch `fix/OPS-005-job-clone-repo`, commit `98abe8e`)

## Verdict
APPROVE

Re-review of commit `98abe8e` (defect D). The prior HIGH — ingest CLI never wired
Cloud SQL IAM token auth, so the Job would fail DB auth (exit 2) even after A/B/C —
is now fixed and the fix faithfully mirrors the service `main.py` lifespan. The prior
MED (premature `RESOLVED` in blockers.md) is corrected. Earlier-approved infra parts
(clone command, Dockerfile `git`, deploy.yml `:latest`) are untouched. Gates green.
Residual runtime risks are all loud-on-failure (not silent) and external to code.

## Findings (this round)

| File:line | Severity | Pattern | Evidence | Status |
|---|---|---|---|---|
| workers/.../ingest/cli.py:115-133 | — | prior HIGH (defect D) | `token_provider = SqlTokenProvider() if settings.cloud_sql_iam_auth else None`; passed as `create_pool(..., token_provider=token_provider)`; `TokenFetchError` added to init `except` → `EXIT_INIT_FAILURE`; `await token_provider.aclose()` in `finally`. Matches `main.py:60-68,101-104`. | RESOLVED |
| docs/blockers.md:190 | — | prior MED | Status now `RESOLVED in code (branch ..., pending merge + redeploy + green execution)`; defect D appended with full RCA. No longer falsely claims end-to-end RESOLVED. | RESOLVED |
| infra/terraform/variables.tf (github_repo) | LOW | config-dependent | Clone URL + WIF share `var.github_repo` → cannot diverge; mismatch → 404 → red (loud). Carried from prior review, no code change. | ACCEPT |

No new findings. No gaming patterns. No swallowed errors (init failure → exit 2;
file errors → exit 1; both logged via `ingest.fatal` / `ingest.summary`).

## Mirror correctness vs main.py (asked point 1)

| Aspect | main.py (service) | cli.py `_run_async` | Match |
|---|---|---|---|
| Provider build | `SqlTokenProvider() if settings.cloud_sql_iam_auth else None` (l.60) | identical (l.115) | ✓ |
| Pass to pool | `create_pool(url, token_provider=...)` (l.62) | identical (l.119) | ✓ |
| Init except incl. TokenFetchError | yes (l.63) | yes (l.120) → `EXIT_INIT_FAILURE` | ✓ |
| Close in finally | `if sql_token_provider is not None: await ...aclose()` (l.103-104) | identical (l.132-133) | ✓ |
| Off-GCP default | `cloud_sql_iam_auth: bool = False` (settings.py:25) → None → password auth | same settings | ✓ |

- `SqlTokenProvider.__init__` (token.py:55) takes only keyword defaults → `SqlTokenProvider()` valid.
- `create_pool` (db.py:31-65) installs the async `password=` callback only when `token_provider` is not None; `normalize_database_url` (db.py:20-21) strips `+asyncpg`, so `DATABASE_URL=postgresql+asyncpg://...` parses for both service and CLI (asked point 6).
- Exit-code semantics (AC-6/AC-7) intact: init/token failure → 2; ≥1 file error → 1; clean → 0. `exec python` in the Job propagates these verbatim.
- `_run_async` body = 34 non-blank lines (≤ 40, clean-code OK). Cyclomatic complexity low.

## Tests (asked point 2)

`workers/tests/test_ingest_cli.py` — 2 new async tests, meaningful, not gamed:
- `test_run_async_passes_token_provider_when_iam_auth_enabled`: `cloud_sql_iam_auth=True`; captures the `token_provider` arg via a fake `create_pool`; asserts `is mock_provider` (identity) AND `aclose` awaited once. Reverting to `create_pool(url)` → captured value `None` → assertion fails. Real regression catcher.
- `test_run_async_passes_none_token_provider_when_iam_auth_disabled`: `cloud_sql_iam_auth=False`; asserts captured `is None` AND `SqlTokenProvider` class `assert_not_called()`.
- Mocking is hermetic: `Settings`, `SqlTokenProvider`, `create_pool`, `Embedder`, `build_chunker`, `process_file` all patched. No real DB, no network, no metadata server.

## Gates (asked point 3)

| Check | Result |
|---|---|
| `uv run ruff check .` | All checks passed |
| `uv run mypy src/` | Success: no issues found in 43 source files |
| `uv run pytest tests/test_ingest_cli.py -q` | 7 passed |
| `uv run pytest -q` (full) | 14 failed, 11 errors — ALL pre-existing DB-integration (asyncpg pool, no local Postgres). NONE in test_ingest_cli.py. No regression introduced. |

## Scope (asked point 5)

Commit `98abe8e` touches exactly 4 files: `cli.py`, `test_ingest_cli.py`,
`blockers.md`, `CHANGELOG.md`. Earlier-approved infra (`deploy.yml`,
`workers.Dockerfile`, `cloud_run_job.tf`, `runbook.md`) UNCHANGED by this commit —
confirmed via `git diff dc52cc4..HEAD --name-only`. Their prior APPROVE stands.
Full branch diff main...HEAD = 224 insertions / 10 deletions across 9 files
(incl. this review). Under 300 LOC. No `specs/` source mutation.

## CHANGELOG (asked point 4)

Fixed bullet rewritten: now "Three defects" + explicit fourth-defect paragraph naming
the IAM token-provider wiring, the `exit 2` failure mode, and the `main.py` mirror.
Accurate. blockers.md status corrected (see Findings).

## Remaining runtime risks (asked point 6) — none blocking

| Risk | Assessment |
|---|---|
| DATABASE_URL `+asyncpg` under IAM | Handled — `normalize_database_url` strips suffix before asyncpg. |
| Network egress for clone | Cloud Run default egress reaches public GitHub over HTTPS. Failure → non-zero → red (loud). |
| `/srv/repo` writable | Container runs as root (no `USER` in Dockerfile) → writable. |
| Mistral key load | `MISTRAL_API_KEY` via `secret_key_ref`; `Embedder()` constructed in init `try` → failure → exit 2 (loud), not swallowed. |
| `/app/.venv/bin/python` path | Plausible (uv default env); failure surfaces non-zero. Carried LOW from prior review. |

All residual risks fail loud (non-zero exit → execution Failed → red workflow),
consistent with AC-7. None silently green. None require a code change in this ticket.

## Bottom line
Defect D fixed correctly and tested. blockers.md honest. No regressions, no gaming,
no scope creep, no security issue. APPROVE. Final green-at-runtime is gated only on
merge + redeploy + a real `Succeeded` execution, as blockers.md now correctly states.
