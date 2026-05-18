# Review — INFRA-002d

## Round 2 (commit `08e2ea5`)

### Verdict R2
APPROVE

### Resolution table

| R1 finding | R2 status | Evidence |
|---|---|---|
| HIGH-1 `main.py:51 Embedder(settings.embedding_model)` → stale `"BAAI/bge-m3"` | RESOLVED | `main.py:53` now `app.state.embedder = Embedder()`. `embedder.py:50` default `model: str = DEFAULT_MODEL_NAME` and `DEFAULT_MODEL_NAME = "mistral-embed"` (l.16). `settings.embedding_model` default updated to `"mistral-embed"` (settings.py:35) with comment marking `DEFAULT_MODEL_NAME` as single source of truth. `except` narrowed to `(ValueError, OSError)` (main.py:54). Auth/RuntimeError no longer swallowed. Regression test `test_lifespan_embedder_model_is_mistral_embed` (test_main_lifespan.py:69-105) asserts `app.state.embedder.model_name == DEFAULT_MODEL_NAME` end-to-end through lifespan. |
| HIGH-2 `api_key: str \| None` | RESOLVED | `embedder.py:51` signature is now `api_key: SecretStr \| None = None`. `embedder.py:62` forwards via `SecretStr(api_key.get_secret_value())` (minor: re-wrap of an already-SecretStr — harmless, mirrors `services/llm.py:71` pattern). All call sites updated: `test_embedder.py:62/71/99/133/145`, `test_embedder_properties.py:44`. mypy strict clean (38 files). |
| MED-3 missing timeout | RESOLVED | `EMBED_TIMEOUT_S: Final = 30` constant (embedder.py:20) with comment citing security.md A04 and mirroring `LLM_TIMEOUT_S`. `kwargs["timeout"] = EMBED_TIMEOUT_S` passed to `MistralAIEmbeddings` (l.58). Test `test_embed_timeout_constant` (test_embedder.py:74-76) + runtime assertion `test_client_has_timeout_and_retries` asserting `emb.client_timeout == EMBED_TIMEOUT_S` (l.94-101). |
| MED-4 missing retries | RESOLVED | `_EMBED_MAX_RETRIES: Final = 3` constant (embedder.py:21). `kwargs["max_retries"] = _EMBED_MAX_RETRIES` (l.59). Test `test_client_has_timeout_and_retries` asserts `emb.client_max_retries >= 1` (l.101). |

### Residual / new observations

| File:line | Severity | Note |
|---|---|---|
| workers/src/archiviste_workers/embedder.py:62 | LOW | `SecretStr(api_key.get_secret_value())` re-wraps an already-SecretStr. Idempotent but redundant — `kwargs["mistral_api_key"] = api_key` would be equivalent (langchain_mistralai accepts SecretStr directly). Non-blocking. |
| workers/src/archiviste_workers/embedder.py:64 | LOW | `base_url + "/v1"` string concat (R1 LOW finding unchanged). Not in mission scope R2. |
| workers/tests/conftest.py:77 | OBS | `_silence_transformers` autouse fixture still present (R1 LOW). Out of mission scope R2. |
| docs/blockers.md ING-016 / `transformers>=4.45` runtime dep | OBS | Documented blocker, deferred to follow-up ticket. Accepted per R1. |

### Tooling status R2
- `uv run pytest`: **124 passed, 18 skipped** (147 s). Skipped suites are Postgres/GCS integration only — env limitation, not test failure.
- `uv run ruff check .`: All checks passed.
- `uv run mypy src/`: Success — no issues found in 38 source files.

### Spec coverage R2
- AC-10 (mistral-embed dim 1024 default): **fully covered**. `test_default_model_name_is_mistral_embed` now constructs `Embedder()` and asserts runtime `model_name`; `test_lifespan_embedder_model_is_mistral_embed` asserts the prod boot path.
- AC-10 (LLM_API_KEY shared via env): **covered** by `test_api_key_env_pickup` (test_embedder.py:80-91).
- AC-10 (image size reduction): partial unchanged (transformers retained, ING-016 follow-up).
- AC-10 (no SQL migration, no re-index): unchanged.

### Diff size R2
- Total origin/main..HEAD: **+298 / -62** across 12 files. Net ~236 LOC. Under the 300 LOC vertical-slice cap.

### Security R2
- secret-hygiene: `api_key: SecretStr` enforced (A09).
- A04: timeout (30 s) + max_retries (3) on Mistral client.
- A03/A10: unchanged (N/A).
- No new secret leak. `"test-key"` literals confined to tests, acceptable.

### Final verdict
**APPROVE** — all R1 HIGH and MED findings closed with code + tests. Residual R1 LOW items (URL concat, conftest dead comment) and OBS items (image size) are non-blocking and tracked. Ready to ship.

---

## Round 1 (initial review)

## Verdict
REQUEST_CHANGES

## Findings

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| workers/src/archiviste_workers/main.py:51 | HIGH | spec violation / runtime bug | `app.state.embedder = Embedder(settings.embedding_model)` — `settings.embedding_model` still defaults to `"BAAI/bge-m3"` (settings.py:32). The new `Embedder.__init__` signature is `(model, api_key, base_url)`, so production will instantiate `MistralAIEmbeddings(model="BAAI/bge-m3")`. The Mistral embeddings API will reject `BAAI/bge-m3` as an unknown model → workers boot path silently sets `embedder=None` via the broad `except Exception` (main.py:52-54) or fails at first retrieve call. AC-10 explicitly requires `mistral-embed` as the default. Tests do not exercise `main.lifespan`, so this is undetected. | Either (a) drop the `settings.embedding_model` field entirely and call `Embedder()` so the constructor default `"mistral-embed"` applies, or (b) change `settings.embedding_model` default to `"mistral-embed"` and add an assertion in `Embedder.__init__` that rejects non-Mistral model names, or (c) add a startup-path test that asserts `app.state.embedder.model_name == "mistral-embed"`. |
| workers/src/archiviste_workers/embedder.py:46 | HIGH | secret-hygiene / security.md A09 | `api_key: str \| None = None` — accepts plain `str`. `.claude/rules/security.md` A09: "ANY token, password, API key … MUST use [`pydantic.SecretStr`]". Compare with `services/llm.py:50` which does use `SecretStr`. Plain `str` will surface in tracebacks, repr, structlog dumps. | Change signature to `api_key: SecretStr \| None = None`, call `.get_secret_value()` when forwarding to `MistralAIEmbeddings(mistral_api_key=SecretStr(...))`. |
| workers/src/archiviste_workers/embedder.py:56 | MED | security.md A04 — missing timeout | `self._client = MistralAIEmbeddings(**kwargs)` — no `timeout` kwarg. security.md A04: "Timeouts on every external call (LLM, GCS, DB): 30s default, hard cap." `services/llm.py:71-79` passes `timeout=LLM_TIMEOUT_S` to every chat client; the embedder regresses this invariant. A network-stuck call will hang the ingest loop and exhaust Cloud Run request budget. | Add `kwargs["timeout"] = EMBED_TIMEOUT_S` (introduce constant, e.g. `30`). Document the value matches `LLM_TIMEOUT_S`. |
| workers/src/archiviste_workers/embedder.py | MED | missing retry/backoff | No retry policy on Mistral HTTP call (5xx, 429, network blip → immediate `encode_batch` failure). Mission scope explicitly asked for "retry/backoff raisonnable". `MistralAIEmbeddings` supports `max_retries` kwarg. | Pass `kwargs["max_retries"] = 3` (or surface as constant). Add a test that asserts the client config has `max_retries >= 1`. |
| workers/src/archiviste_workers/embedder.py:54 | LOW | clean-code / fragile URL composition | `kwargs["endpoint"] = base_url + "/v1"` — string concat to build URL. Trailing slash in `base_url` produces `//v1`. Tests pass because `httpserver.url_for("")` yields no trailing slash, but a `.env` typo `MISTRAL_BASE_URL=https://api.mistral.ai/` breaks silently. | Use `urllib.parse.urljoin` or `base_url.rstrip("/") + "/v1"`. |
| workers/src/archiviste_workers/settings.py:32 | MED | dead config | `embedding_model: str = "BAAI/bge-m3"` is now misleading — it is the literal of the dropped backend, yet still wired into `main.py:51`. Either delete the field or update the default and adjust callers. | Remove field OR set default `"mistral-embed"`. If kept, fail-fast in `Embedder.__init__` when `model not in {"mistral-embed"}`. |
| workers/src/archiviste_workers/main.py:48-54 | LOW | swallowed error (pre-existing, now amplified) | `try: Embedder(...) except Exception: app.state.embedder = None` — comment still references `sentence-transformers`. With Mistral backend the only realistic failure here is bad API key / unknown model, both of which should be **fail-fast** at boot, not silently degraded. The broad-`Exception` swallow now hides the HIGH finding above. | Narrow to `(ValueError, RuntimeError)` and let auth/credential errors propagate so Cloud Run revision fails the smoke test (AC-12). Update the comment to drop the `sentence-transformers` reference. |
| workers/pyproject.toml:28 | OBS | residual `transformers` runtime dep | `transformers>=4.45` kept in `[project.dependencies]` to satisfy `chunker.py::AutoTokenizer`. Documented blocker in `docs/blockers.md` 2026-05-18 entry, follow-up ING-016. Accepted per review mission instructions but image-size goal of AC-10 ("réduction taille image" Touch Points) is only partially achieved. | Track ING-016 follow-up. No action this PR. |
| workers/tests/test_embedder.py | OBS | no negative test for missing api_key | All tests construct `Embedder(api_key="test-key", base_url=...)`. No test verifies behavior when `api_key=None` (prod path that relies on env-var pickup by langchain_mistralai). The "AC-10 LLM_API_KEY shared" claim is not test-backed. | Add 1 test that monkeypatches `MISTRAL_API_KEY` env and constructs `Embedder()` with no explicit key. |
| workers/tests/test_embedder.py:62-64 | OBS | tautology test | `test_default_model_name_is_mistral_embed` asserts `DEFAULT_MODEL_NAME == "mistral-embed"` by reading the same constant the implementation defines. Pure constant-mirror test. | Replace by an integration assertion: instantiate `Embedder()` and verify `embedder.model_name == "mistral-embed"`. |
| workers/tests/conftest.py:77 | LOW | dead comment | `_silence_transformers` autouse fixture remains for "backward compat with optional embedder-fallback extras". Optional extras are not installed in CI; fixture is now noise on every test. | Delete fixture or scope it to `pytest.mark.embedder_fallback`. |

## Spec coverage
- AC-10 (mistral-embed dim 1024 default): partial — test `test_default_model_name_is_mistral_embed` asserts the constant, but `main.py:51` overrides the constructor default with the stale settings field. Acceptance not satisfied end-to-end.
- AC-10 (LLM_API_KEY shared via env): unverified — no test exercises the env-var pickup path.
- AC-10 (image size reduction): partial — `sentence-transformers` (≈2 GiB) dropped from runtime ✓; `transformers` retained per documented blocker. Net gain real but below stated goal.
- AC-10 (no SQL migration, no re-index): ✓ — no migration touched, no chunk re-encoding logic.

## Property invariants
- INV-2 (embedding dim constant): ✓ covered by `test_embeddings_share_constant_dim` (test_embedder_properties.py:62) with mock returning 1024-dim. Property does not exercise real Mistral output shape variance — acceptable since the dim assertion in `encode_batch` is the runtime guard.

## Security
- secret-hygiene: `api_key` not wrapped in `SecretStr` — HIGH finding above.
- A03 SQL injection: N/A (no SQL touched).
- A04 timeout: missing on Mistral call — MED finding above.
- A04 rate limit / A01 access control: N/A (no public route).
- A10 SSRF: `base_url` is operator-controlled (not user-supplied), no SSRF surface.
- No secrets in diff (verified: only `"test-key"` literals in tests, acceptable per security.md "Tests use fixtures").
- `gitleaks`-grade scan: clean.

## Out-of-scope changes
- `workers/src/archiviste_workers/settings.py` — modified (comment-only) but NOT in plan PR-d "Files to touch" (plan line 52 explicitly: "aucun changement champ ; commentaire explicite"). Acceptable per plan wording.
- `workers/tests/conftest.py` — modified (comment-only) but NOT in plan PR-d "Files to touch". Trivial.

## Tooling status
- `uv run pytest tests/test_embedder.py tests/test_embedder_properties.py` — 10 passed.
- `uv run ruff check .` — All checks passed.
- `uv run mypy src/` — Success: no issues found in 38 source files.
- Full `uv run pytest` not executed in this review (focused scope). Recommend implementer runs full suite to verify `main.py` lifespan path is not broken by the `embedding_model="BAAI/bge-m3"` mismatch.

## Ragas eval baseline
- Not exercised by this PR. Live marker `pytest -m live` skipped by default per plan. Cannot certify ≥ baseline without a run targeting real Mistral API + golden set — out of band of this review per existing ticket scoping.
