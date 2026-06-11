# Review — EVAL-010

## Verdict
APPROVE

## Summary
Lean fix (diff 200 LOC, well within 300). Adds Cloud SQL IAM-token auth to `eval/persist.py`
gated on `CLOUD_SQL_IAM_AUTH=="true"`, threads safe `error_class`/`pgcode` through `PersistError`,
and logs metric floats as `persist_attempt` before the write. Lints + tests green. Token never
reaches any log line, exception string/repr, or traceback. No gaming patterns found.

## Lint / test gate
| Gate | Result |
|---|---|
| `uv run --extra dev ruff check .` | All checks passed |
| `uv run --extra dev mypy persist.py ragas_runner.py` | Success: no issues (no `# type: ignore`) |
| `uv run --extra dev pytest -q` | 148 passed, 1 skipped (DATABASE_URL integration), 0 failed |

## Findings

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| eval/persist.py:108 | LOW | sensitive type not wrapped | `token: str = cast(str, creds.token)` returns a bare `str`, not `pydantic.SecretStr`. Workers reference `auth_metadata/token.py:44,109` wraps the bearer in `SecretStr` per security.md §A09. Here the token never enters a log/repr (verified), so leakage risk is nil, but the type itself is not self-defending. | Wrap in `SecretStr` and `.get_secret_value()` at the `psycopg2.connect(password=...)` call site for defense-in-depth parity with workers. Non-blocking. |
| eval/ragas_runner.py:437 | LOW | stringly-typed sentinel | NullMetrics path passes literal `error_class="NullMetrics"` — diverges from the `type(exc).__name__` convention and is a magic string not sourced from a const. | Acceptable; minor. Optionally name a const. |

## Token-leakage audit (primary hunt — security.md §A09)
- `_fetch_iam_db_token()` returns the token to a single local `iam_token` (persist.py:143), passed only as `password=` kwarg to `psycopg2.connect` (persist.py:146). Not stored on any object, not returned to caller.
- `PersistError.__init__` takes only `message` (static `"eval_runs insert failed"`), `error_class`, `pgcode`. Token cannot enter it. Verified by test `test_iam_token_not_in_persist_error_string` asserting token absent from `str(err)` and `repr(err)`.
- On `psycopg2.connect` failure, the raised psycopg2 exception does not embed the `password` kwarg; even so it is chained via `from exc` but NEVER logged: `_maybe_persist` catches `PersistError` and logs only `error_class`/`pgcode` (ragas_runner.py:454). No `exc_info=True` on any persist-path log call. No top-level traceback dump catches an escaping exception (grep of `main()` exception handlers: only `PersistError`, `ValueError`, `OidcTokenError`, none re-printing the chain).
- structlog default config, no ProcessorFormatter dumping exc chains.
- Verdict: token cannot reach Cloud Logging. CLEAN.

## Correctness audit
- IAM branch fires ONLY when `os.getenv("CLOUD_SQL_IAM_AUTH") == "true"` (persist.py:122,142). Exact string match — `"True"`/`"1"` correctly do NOT trigger. Verified by `test_iam_auth_injects_token_as_password` (true → password kwarg present) and `test_no_iam_auth_no_password_kwarg` (unset → `"password" not in kwargs`). Behaviour unchanged for local/CI.
- `getattr(exc, "pgcode", None)` is safe: returns `None` for non-psycopg2 exceptions (e.g. the `NullMetrics` path never calls this; `ValueError` would yield `None`). psycopg2 errors expose `.pgcode`. Annotated `pgcode: str | None`. Verified by `test_persist_error_carries_error_class_and_pgcode` (pgcode `"08006"`).
- Module-top `import google.auth` / `google.auth.transport.requests`: safe — `google-auth>=2` is a HARD dependency in `eval/pyproject.toml:8` (not optional), present in dev + live + base. No CI ImportError. Import cost is negligible vs a one-shot eval job.
- Token TTL vs connection lifetime: NON-ISSUE. Eval is a short one-shot (one connect → one INSERT → close, persist.py:142-165), unlike the long-lived workers pool that needs refresh-ahead. A freshly-refreshed token (`creds.refresh(request)`) far outlives one connection. No caching needed; correctly NOT premature-abstracted.

## Spec / contract coverage (AC comments in test_persist.py)
- AC-6 (golden_set_version determinism + 1-byte sensitivity): test `test_golden_set_version_determinism`, `test_golden_set_version_one_byte_mutation`
- AC-7 (append-only, two INSERTs distinct ids, no UPSERT): `test_two_persist_calls_produce_two_inserts`, `test_insert_sql_contains_no_update_or_upsert`
- AC-8 (INSERT parameterised, no f-string/format): `_INSERT_SQL` is a static literal with `%s` placeholders (persist.py:33-40); `cursor.execute(_INSERT_SQL, params)` (persist.py:151). No interpolation added by this diff. Static-source grep test present.
- AC-9 (no LLM payload): `EvalRunRow` unchanged — only metrics + metadata, no answer/question/contexts/citations. Diff adds zero payload fields.
- AC-10 (DATABASE_URL no fallback): `os.environ["DATABASE_URL"]` (persist.py:121) — raises KeyError, no default. secret-hygiene §Production satisfied.
- AC-11 (connect raises → PersistError → exit 4): `test_maybe_persist_returns_4_on_persist_error`, `test_maybe_persist_returns_4_when_metrics_are_none`
- EVAL-010 (IAM token as password, never logged): `test_iam_auth_injects_token_as_password`, `test_no_iam_auth_no_password_kwarg`, `test_iam_token_not_in_persist_error_string`, `test_persist_error_carries_error_class_and_pgcode`

## Test quality (gaming hunt)
- Mocks are NOT loose: `test_iam_auth_injects_token_as_password` captures the actual `connect` kwargs and asserts `password == "fake-iam-token"` — would FAIL if the token were dropped. `test_no_iam_auth_no_password_kwarg` asserts the inverse — would FAIL if password were always passed. Both directions pinned.
- `_FakeCreds.refresh` sets a known token; the assertion checks that exact value flows through. No tautology.
- No `@pytest.mark.skip`/`xfail`/`#[ignore]` added. The 1 skip is the pre-existing CLI integration test gated on `DATABASE_URL` (justified).
- No hardcoded bypass, no swallowed error, no `# type: ignore`.

## Security
- No secrets in code (token fetched at runtime from ADC; test uses fixture `"fake-iam-token"`).
- SQL: parameterised, unchanged.
- A09: token never logged — verified above.
- No SSRF/CORS/CSP surface (eval job, no public HTTP route).

## clean-code.md
- `_fetch_iam_db_token` 8 lines body, `persist_eval_run` ~27 lines body — both ≤40.
- Naming verb/noun-correct (`use_iam_auth` predicate, `_fetch_iam_db_token` verb).
- `_CLOUD_SQL_IAM_SCOPE` named const, not magic. No dead code, no premature caching abstraction.

## Out-of-scope changes
- None. Touches `eval/persist.py`, `eval/ragas_runner.py`, `eval/tests/test_persist.py`, `CHANGELOG.md` — all consistent with ticket intent. No specs/ or migrations/ modified.
