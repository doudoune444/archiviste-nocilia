# Review — EVAL-003

## Verdict
APPROVE

_Final verdict after re-review. The initial pass returned REQUEST_CHANGES (one MED defect + LOWs); all findings were resolved and confirmed in the re-review below. Original findings preserved for the record._

## Initial review verdict (superseded)
REQUEST_CHANGES

## Local gates (worktree)
| Gate | Result |
|---|---|
| `uv run ruff check .` | PASS (All checks passed) |
| `uv run mypy --config-file pyproject.toml .` | PASS (27 files, no issues) |
| `uv run pytest tests/` | PASS (123 passed, 1 skipped) |
| `uv lock --check` | PASS (lock up to date) |

Skip = `test_runner_persist_cli.py:257` gated on `DATABASE_URL` unset — pre-existing, not introduced here, justified.

## Findings (HIGH first)

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| eval/metrics.py:192-194 | MED | judge identity drift / re-resolved independently of builder | `judge_identity` recomputes `provider`/`chat_model` from env instead of taking them from `build_ragas_judge()`. For `RAGAS_JUDGE_PROVIDER=openai` with no `RAGAS_JUDGE_MODEL` override, `_build_openai_judge` uses `gpt-4o` (line 142) but `judge_identity["chat_model"]` records `DEFAULT_MISTRAL_JUDGE_MODEL` = `mistral-large-2411`. The recorded judge identity is WRONG for the openai-default path. | Have `build_ragas_judge()` return the resolved `(provider, chat_model_id)` (plan H1 explicitly says "surface resolved (provider, chat_model_id)") and build `judge_identity` from that single source of truth — not a second independent env read. |
| eval/metrics.py:97,121 | LOW | dead defensive branch / no-workaround smell | `raw_key = api_key.get_secret_value() if hasattr(api_key, "get_secret_value") else ""`. `api_key` is always a `SecretStr` constructed at line 89; the `hasattr`/`else ""` branch is unreachable. `api_key: Any` typing + this guard exist only to satisfy the loose signature. No test exercises the `else`. | Type the param `api_key: SecretStr` and drop the `hasattr` guard; call `.get_secret_value()` directly. Removes a dead branch (clean-code: no dead code) and tightens types. |
| eval/metrics.py:87-92 | LOW | redundant SecretStr round-trip | `build_ragas_judge` wraps key in `SecretStr` (line 89), passes it as `Any`, then each `_build_*` does `.get_secret_value()` and re-wraps in a NEW `SecretStr` (lines 121-122 / 143-144). The first wrap is pointless — the raw string is immediately unwrapped one frame down. | Pass the `SecretStr` through unchanged (do not unwrap+rewrap), or read the env directly inside each builder. Net: the key is never needlessly materialized as a bare `str` in `build_ragas_judge`'s frame. |
| eval/tests/test_ragas_judge.py:183-184 | LOW | weak AC-5 assertion | Test asserts only `"llm" in captured_kwargs` / `"embeddings" in captured_kwargs`. Spec oracle AC-5 requires the captured objects be "égaux aux objets produits par la fonction de sélection". Presence-only check would still pass if the wrong objects were passed. | Assert `isinstance(captured_kwargs["llm"], LangchainLLMWrapper)` and `isinstance(captured_kwargs["embeddings"], LangchainEmbeddingsWrapper)` (cannot assert identity since builder is called internally, but type-shape closes the gap). |

## Spec coverage
- AC-1: PASS · `test_build_judge_default_is_mistral` (unset provider → mistral couple).
- AC-2: PASS · `test_build_judge_mistral_llm_type` + `test_build_judge_mistral_embeddings_type` assert `LangchainLLMWrapper(ChatMistralAI)` + `LangchainEmbeddingsWrapper(MistralAIEmbeddings)`. Matches amended AC-2 (embeddings wrapper named). Impl lines 119-124.
- AC-3: PASS · `test_build_judge_openai_llm_type` + `test_build_judge_openai_embeddings_type`. Matches amended AC-3.
- AC-4: PASS · `test_build_judge_unknown_provider_raises` (message contains `anthropic`, `mistral`, `openai`) + `test_unknown_provider_no_ragas_evaluate_call` (asserts `ragas.evaluate` not called). `ValueError` raised before any evaluate call (metrics.py:99-101). NOTE: the no-call test is slightly theatrical — it patches `ragas.evaluate` and calls `build_ragas_judge()`, which never references `ragas.evaluate` regardless; the assertion can't fail. Still, it does encode AC-4 intent. LOW.
- AC-5: PARTIAL · `test_run_ragas_evaluate_passes_judge_to_ragas` proves `llm=`/`embeddings=` kwargs are passed, but only presence, not object shape (see LOW finding). Wiring itself correct (metrics.py:215-216).
- AC-6: PASS · default chat = `DEFAULT_MISTRAL_JUDGE_MODEL`, default embeddings = `mistral-embed`, both overrides covered by 4 tests (192-243).
- AC-7: PASS · `test_api_key_absent_from_llm_repr`, `_embeddings_repr`, `_captured_logs`, `test_build_run_dict_judge_no_api_key`. Key via `SecretStr`; judge identity dict carries only `{provider, chat_model}`, never the key. `SECRET_ENV_VARS` in run_writer.py already redacts `LLM_API_KEY` as defense-in-depth.
- AC-8: PASS · `langchain-mistralai>=0.2` in both `live` and `dev` extras (pyproject.toml:23,34); `uv.lock` regenerated (resolves `1.1.4`); `uv lock --check` clean.
- AC-9: PASS · README documents default `mistral`, `RAGAS_JUDGE_MODEL` / `RAGAS_JUDGE_EMBEDDINGS_MODEL`, pinned snapshot, and explicit EVAL-001 AC-14 supersession note.
- AC-10: PASS (for mistral-default; see MED finding for openai) · `test_build_run_dict_judge_field_present` / `_absent_when_none` prove additive emission: judge field present when set, omitted when `None` (offline). No DB column / migration (run_writer.py:62-63, runner offline path returns `judge=None`).

## Property invariants
- `specs/properties.md` not consulted for new invariants — ticket is config-object construction with no stated property invariant (spec Performance/SLO: "Non gated"). No gap.

## Security
- Secrets: no real credentials. Test fixtures `sk-secret-test-key-do-not-log` / `sk-openai-fake-key` are obviously-fake literals (secret-hygiene allows fixtures). No high-entropy real key in diff.
- SecretStr: `LLM_API_KEY` wrapped in `pydantic.SecretStr`; never `.get_secret_value()` into logs, run JSON, or judge identity dict (A09 satisfied). Raw key only materialized to pass into LangChain client constructors, which themselves redact.
- SSRF / SQL / CORS / JWT / path traversal: N/A — no network handler, no query, no URL, no template, no FS path in diff.
- A06: `langchain-mistralai` from official PyPI, < 1k LOC, no FFI → no ADR required (spec Non-goal confirms). Lock committed.

## Scope
- Touched files = exactly the plan's "Files to touch" (metrics.py, run_writer.py, ragas_runner.py, pyproject.toml, uv.lock, tests/test_ragas_judge.py, README.md, CHANGELOG.md) + the approved `specs/acceptance/EVAL-003.md` AC-2/AC-3 amendment.
- MUST-NOT-TOUCH verified UNCHANGED: `eval/gates.py`, `eval/baseline.json`, `migrations/**` (empty `git diff`).
- Additive-only `RunFile.judge`: default `None`, offline leaves `judge=None`, no DB column / migration. Confirmed.
- Diff: 207 insertions / 33 deletions across non-generated files (`uv.lock` excluded) — well under 300 LOC.

## Notes (non-blocking)
- Installed `ragas` resolves to 0.4+ where `LangchainLLMWrapper` / `LangchainEmbeddingsWrapper` emit `DeprecationWarning`. They still function (tests green) and the spec/`>=0.2` constraint is met, but the chosen wrapper API is on a deprecation path. Flag for the human follow-up re-baseline ticket; not a blocker here.

## Rationale for REQUEST_CHANGES (not APPROVE)
The MED judge-identity-drift defect means the AC-10 run-file field records a model that was not actually used whenever `RAGAS_JUDGE_PROVIDER=openai` runs without `RAGAS_JUDGE_MODEL`. Since the whole point of the pinned-snapshot design is reproducibility/traceability of the judge, a record that lies about the judge model undermines the ticket's core intent. Fix is small (thread the resolved identity out of `build_ragas_judge()` per plan H1). Not BLOCK: gates are green, mistral-default path (the shipped default) is correct, no security issue.

VERDICT: REQUEST_CHANGES

---

## Re-review (2026-06-09)

Re-review after implementer fixes. Original findings above kept for the record.

### Local gates (worktree `eval/`)
| Gate | Result |
|---|---|
| `uv run ruff check .` | PASS (All checks passed) |
| `uv run mypy --config-file pyproject.toml .` | PASS (27 files, no issues) |
| `uv run pytest tests/` | PASS (124 passed, 1 skipped) — +1 vs prior 123 (new MED regression test) |

Skip = `test_runner_persist_cli.py:257` gated on `DATABASE_URL` unset — pre-existing, justified.

### Prior findings — resolution
| Finding | Sev | Status | Evidence |
|---|---|---|---|
| judge identity re-resolved from env independently of builder | MED | RESOLVED | `metrics.py:88-110` new `_build_ragas_judge_with_identity()` is the SINGLE env-resolution point: reads `RAGAS_JUDGE_PROVIDER` + key once, dispatches to `_build_mistral_judge`/`_build_openai_judge` which each return `(llm, embeddings, chat_model_id)`. `_run_ragas_evaluate` (metrics.py:212-214) consumes `(llm, embeddings, provider, chat_model)` from that one call and builds `judge_identity = {"provider", "chat_model"}` from it — no second env read. `build_ragas_judge()` is now a thin wrapper over the same source. Each `_build_*` resolves `chat_model` via env once and returns it, so the recorded id always equals the model handed to the LangChain client. |
| dead `hasattr` SecretStr guard / `api_key: Any` | LOW | RESOLVED | `git grep hasattr / api_key: Any / get_secret_value` in `metrics.py` → 0 hits. `_build_mistral_judge`/`_build_openai_judge` params typed `api_key: SecretStr` (metrics.py:113,135). |
| redundant SecretStr unwrap+rewrap round-trip | LOW | RESOLVED | `SecretStr(os.environ.get("LLM_API_KEY",""))` built once (metrics.py:108), passed through unchanged to `ChatMistralAI(api_key=api_key)` / `ChatOpenAI(api_key=api_key)` etc. No `.get_secret_value()` anywhere in `metrics.py`; key never materialized as bare `str` in builder frames. |
| weak AC-5 assertion (kwarg presence only) | LOW | RESOLVED | `test_ragas_judge.py:187-192` now asserts `isinstance(captured_kwargs["llm"], LangchainLLMWrapper)` and `isinstance(captured_kwargs["embeddings"], LangchainEmbeddingsWrapper)` — object shape, not mere presence. |
| theatrical unknown-provider no-call test | LOW | RESOLVED | `test_unknown_provider_no_ragas_evaluate_call` (test_ragas_judge.py:126-135) no longer patches `ragas.evaluate`; it asserts the `ValueError` (not RuntimeError) propagates from `build_ragas_judge()` before any judge objects are produced — a real, falsifiable check of AC-4's "no evaluate" intent. |

### MED regression test — verified
`test_run_ragas_evaluate_openai_judge_identity_records_openai_model` (test_ragas_judge.py:348-400): sets `RAGAS_JUDGE_PROVIDER=openai`, pops `RAGAS_JUDGE_MODEL`, mocks `ragas.evaluate`, calls `_run_ragas_evaluate`, asserts `judge_identity["provider"] == "openai"`, `judge_identity["chat_model"] == "gpt-4o"`, and `!= DEFAULT_MISTRAL_JUDGE_MODEL`. Passes. This is exactly the openai-default-path drift the prior MED flagged; it would fail under the old re-derive-from-env code. Confirmed there is exactly one env-resolution of the chat model per provider path, reused for both the client and the identity.

### No-regression re-confirmation
- All 10 ACs still met (AC-1..AC-9 unchanged from prior PASS; AC-10 now correct for BOTH mistral-default and openai paths).
- AC-7: `LLM_API_KEY` never serialized — `get_secret_value()` absent from `metrics.py`; `judge_identity` dict carries only `{provider, chat_model}`; `test_build_run_dict_judge_no_api_key` asserts fake key absent from JSON; `SECRET_ENV_VARS` redaction in `run_writer.py` intact (defense-in-depth).
- Scope clean: `eval/gates.py`, `eval/baseline.json`, `migrations/**` show EMPTY `git diff HEAD`. Only approved AC-2/AC-3 spec amendment touched under `specs/acceptance/`.
- Diff ~179 ins / 32 del (excl. `uv.lock`) — under 300 LOC.

### Notes (non-blocking, unchanged)
- Ragas `ragas.metrics.*` imports emit `DeprecationWarning` (deprecated in favor of `ragas.metrics.collections`); tests green, `>=0.2` constraint met. Flag for human follow-up re-baseline ticket; not a blocker.

## Re-review verdict
APPROVE — all 5 prior findings (1 MED, 4 LOW) resolved with evidence; MED fix backed by a falsifiable regression test; gates green; scope clean; no new findings; no security issue.

VERDICT: APPROVE
