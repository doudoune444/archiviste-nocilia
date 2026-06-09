# Plan — EVAL-003 Juge Ragas configurable Mistral (default mistral, pinned snapshot)

## Goal
Wire a configurable Ragas judge into the live eval path so `ragas.evaluate()` runs under an explicit `(llm, embeddings)` couple selected by `RAGAS_JUDGE_PROVIDER` (default `mistral`, pinned chat snapshot), and record the resolved judge identity in the run file.

## Acceptance criteria recap
- AC-1 : `eval/metrics.py` expose une fonction de construction du juge qui, selon `RAGAS_JUDGE_PROVIDER` (valeurs `mistral` | `openai`, défaut `mistral` en l'absence de la variable), retourne le couple `(llm, embeddings)` à passer à `ragas.evaluate(llm=..., embeddings=...)`.
- AC-2 : Pour `RAGAS_JUDGE_PROVIDER=mistral` (ou non-défini), la fonction construit un `ragas.llms.LangchainLLMWrapper` enveloppant un `langchain_mistralai.ChatMistralAI` et un `langchain_mistralai.MistralAIEmbeddings`.
- AC-3 : Pour `RAGAS_JUDGE_PROVIDER=openai`, la fonction construit l'équivalent OpenAI (`langchain_openai.ChatOpenAI` enveloppé en `LangchainLLMWrapper` + `langchain_openai.OpenAIEmbeddings`).
- AC-4 : Une valeur de `RAGAS_JUDGE_PROVIDER` hors `{mistral, openai}` fait lever une erreur explicite citant la valeur reçue et l'ensemble des valeurs autorisées ; aucun appel `ragas.evaluate()` n'a lieu.
- AC-5 : `ragas.evaluate()` est invoqué avec les objets `llm=` et `embeddings=` produits par la fonction de sélection (le run live n'utilise plus le juge OpenAI implicite par défaut de Ragas).
- AC-6 : Le modèle chat du juge `mistral` est par défaut un snapshot daté pinné (ex. `mistral-large-2411`, id exact confirmé à l'impl), surchargeable via `RAGAS_JUDGE_MODEL` ; le modèle d'embeddings du juge `mistral` est `mistral-embed`, surchargeable via `RAGAS_JUDGE_EMBEDDINGS_MODEL`.
- AC-7 : La clé d'API du juge est lue depuis l'env var existante `LLM_API_KEY` via `pydantic.SecretStr`, et n'apparaît jamais dans les logs stdout/stderr, le fichier de run, ni l'artefact CI (cohérent EVAL-001 AC-16).
- AC-8 : `eval/pyproject.toml` ajoute `langchain-mistralai>=0.2` aux extras `live` et `dev`, et `eval/uv.lock` est régénéré en cohérence.
- AC-9 : `eval/README.md` documente le défaut effectif `mistral`, les env vars `RAGAS_JUDGE_PROVIDER` / `RAGAS_JUDGE_MODEL` / `RAGAS_JUDGE_EMBEDDINGS_MODEL`, le snapshot chat pinné par défaut, et note explicitement la supersession du défaut `openai` de EVAL-001 AC-14.
- AC-10 : Le fichier de run JSON contient un champ d'identité du juge enregistrant le provider effectif et l'id de modèle chat résolu ; pour un run `mistral` par défaut, le champ vaut le provider `mistral` et l'id du snapshot chat pinné effectivement utilisé.

## Key hypotheses (confirmed pre-flight)
- H1: New `build_ragas_judge() -> (llm, embeddings)` lives in `eval/metrics.py`, slotted into `_run_ragas_evaluate` which passes `llm=`/`embeddings=` to `ragas.evaluate()`. All `langchain_*` / `ragas.llms` imports stay lazy (inside the function, `# noqa: PLC0415`) — module import stays dep-free for offline/CI.
- H2: Judge identity is an additive field on `RunFile` (`judge: dict | None = None`, default `None` → offline), serialized via `_build_run_dict`. No DB column, no migration, no `eval_runs` change.
- H3: Ragas `>=0.2` accepts `LangchainLLMWrapper(chat)` for `llm=` and a LangChain embeddings object for `embeddings=` (shape confirmed at impl — see OQ-3).

## Files to touch
- `eval/metrics.py` — add `build_ragas_judge()` (provider select + pinned-default model resolution, SecretStr key) + `DEFAULT_MISTRAL_JUDGE_MODEL` const; wire `llm=`/`embeddings=` into `_run_ragas_evaluate`; return resolved `(provider, chat_model_id)` for judge identity.
- `eval/run_writer.py` — additive `RunFile.judge: dict[str, str] | None = None`; emit in `_build_run_dict` only when set.
- `eval/ragas_runner.py` — populate `run.judge` from the resolved judge identity on the live path (built in `_build_run`/`_resolve_ragas_metrics` plumbing); offline leaves `judge=None`.
- `eval/pyproject.toml` — add `langchain-mistralai>=0.2` to `live` and `dev` extras.
- `eval/uv.lock` — regenerate via `uv lock` (IMPLEMENTER step, not architect; excluded from LOC budget).
- `eval/tests/test_ragas_judge.py` — NEW: unit tests AC-1..AC-7, AC-10 (`_build_run_dict` level).
- `eval/README.md` — judge env vars, default `mistral`, pinned snapshot, EVAL-001 AC-14 supersession note.
- `CHANGELOG.md` — `## [Unreleased]` EVAL entry.

### MUST-NOT-TOUCH (no-change invariants)
- `eval/gates.py` — judge wiring only; Gate A/B thresholds untouched (Non-goal). Left UNREAD.
- `eval/baseline.json` — no re-bake in this ticket (Non-goal). Left UNREAD.
- `specs/*` (acceptance/openapi/properties/golden_qa), `migrations/*` — human-only, untouched.

## Test strategy
- Unit (deterministic, no network), in `eval/tests/test_ragas_judge.py`:
  - AC-1: unset `RAGAS_JUDGE_PROVIDER` → mistral couple returned.
  - AC-2/AC-3: assert wrapper/type shape per provider (type/attr inspection, no network).
  - AC-4: `RAGAS_JUDGE_PROVIDER=anthropic` → error raised, message contains received value + `mistral|openai`; assert `ragas.evaluate` (monkeypatched) NOT called.
  - AC-5: monkeypatch `ragas.evaluate` → assert called with `llm=`/`embeddings=` equal to `build_ragas_judge()` outputs.
  - AC-6: default → chat = pinned snapshot, embeddings = `mistral-embed`; with overrides set → attrs reflect override.
  - AC-7: `LLM_API_KEY=sk-secret-xxx` → assert substring absent from `repr()`/captured log/serialized run dict (SecretStr).
- Integration (AC-10): build a `RunFile` with `judge` set, run through `_build_run_dict` → assert provider `mistral` + resolved chat id present; assert no DB/`eval_runs` write involved. NOT a full `main()` live seam (LOC + live path stays workflow_dispatch-only, untested in CI — confirmed).
- Contract (AC-8/AC-9): grep `langchain-mistralai` in both extras; grep README for the three env vars + supersession note.
- No live `ragas.evaluate()` in CI; full live path remains `workflow_dispatch`-only.

## Implementation steps (ordered, tests-first per vertical-slice.md)
1. `eval/tests/test_ragas_judge.py` — write failing unit tests AC-1..AC-7 against `build_ragas_judge()` (lazy-import-aware, monkeypatch `ragas.evaluate`).
2. `eval/metrics.py` — add `DEFAULT_MISTRAL_JUDGE_MODEL` const + `build_ragas_judge()`; resolve provider/model/key (SecretStr); make AC-1..AC-4, AC-6, AC-7 pass.
3. `eval/metrics.py` — wire `llm=`/`embeddings=` into `_run_ragas_evaluate` and surface resolved `(provider, chat_model_id)`; make AC-5 pass.
4. `eval/run_writer.py` — additive `RunFile.judge` + `_build_run_dict` emission; add AC-10 test on `_build_run_dict`; make it pass.
5. `eval/ragas_runner.py` — populate `run.judge` on live path; offline `judge=None`.
6. `eval/pyproject.toml` — add `langchain-mistralai>=0.2` to `live`+`dev` (AC-8 grep).
7. IMPLEMENTER: `uv lock` to regenerate `eval/uv.lock`; verify `uv lock --check` / `--frozen`.
8. `eval/README.md` — judge docs + supersession note (AC-9).
9. `CHANGELOG.md` — `## [Unreleased]` EVAL entry.

## Risks / open questions
- OQ-1 (impl-time decision): pin `DEFAULT_MISTRAL_JUDGE_MODEL` constant; `mistral-large-2411` proposed, exact snapshot id confirmed against Mistral docs at impl. No observable AC fixes the precise id; must be settled before committing the default.
- OQ-3 (impl-time decision): resolve exact embeddings/LLM wrapper shape against the LOCKED Ragas version — does `ragas>=0.2` accept raw LangChain embeddings or require a dedicated wrapper (e.g. `LangchainEmbeddingsWrapper`)? If the Ragas API diverges from H3 → STOP, log to `docs/blockers.md` per `.claude/rules/no-workaround.md`. Do NOT patch around (no `# type: ignore`, no shim).
- AC-7: `LLM_API_KEY` MUST flow as `pydantic.SecretStr`; never `.get_secret_value()` into logs/run/artefact. `SECRET_ENV_VARS` in `run_writer.py` already covers `LLM_API_KEY` redaction as defense-in-depth — verify judge identity field carries only provider + model id, never the key.

## LOC budget
~250–290 LOC incl. tests, ≤ 300 (vertical-slice.md). `eval/uv.lock` regen excluded. Single vertical slice — no split needed.

## Out of scope
- No first Mistral live run, no `eval/baseline.json` re-bake, no Gate A recalibration (human follow-up, needs paid live run).
- No `eval/gates.py` / threshold change.
- No generation-path change (`/v1/generate`, `LLM_PROVIDER`, system prompt).
- No live network call in CI (`workflow_dispatch`-only).
- No DB column / migration / `eval_runs` persistence of judge identity (run-file only).
- No Terraform, no OpenAPI change, no Cloud Run image rebuild (OBS-008), no ADR.
