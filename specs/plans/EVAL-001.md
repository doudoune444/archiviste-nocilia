# Plan — EVAL-001 Runner Ragas golden set + gates CI

## Pre-flight (CLAUDE.md workflow rule 2)

### (a) Fichiers / dirs lus
- `specs/acceptance/EVAL-001.md` (AC-1..AC-17, non-goals, failure modes, touch points, SLO)
- `specs/properties.md` (aucun INV applicable phase 1)
- `specs/openapi/gateway-to-workers.yml` (contrats `/v1/retrieve` + `/v1/generate`)
- `specs/golden_qa.jsonl` (46 entrées confirmées via grep `"id":` = 46)
- `eval/ragas_runner.py` (skeleton ~96 LOC à remplacer), `eval/baseline.json` (zeros initiaux), `eval/seed_test_corpus.py` (stub)
- `.github/workflows/eval.yml` (workflow existant à réécrire)
- `.gitignore` (ligne 99 `specs/golden_qa.jsonl` gitignored — résolu via fixture CI dédié, cf (c) OQ-1)
- `workers/src/archiviste_workers/{retrieve,generate}/{router,models,schemas}.py` (signatures réelles)
- `workers/pyproject.toml` (ragas>=0.2, datasets>=3.2 déjà en optional `dev`)
- `workers/tests/conftest.py` (patterns fixtures)
- `specs/plans/GEN-001.md` (référence patterns)

### (b) Hypothèses clés du plan
1. **Split non requis** : le scope tient en ≤ 300 LOC en gardant les helpers Ragas factorisés (loader 50, clients 60, stub 20, métriques 80, gates 50, runner CLI 60, baseline init 10 ≈ 330). Si la mesure réelle dépasse 300 LOC hors tests, split `EVAL-001a` (offline + gate B + workflow PR) / `EVAL-001b` (live + gate A + workflow_dispatch).
2. **Ragas en mode offline** : `ragas.evaluate()` avec `LLMContextRecall`, `LLMContextPrecisionWithReference`, `Faithfulness`, `AnswerRelevancy` requiert un LLM-judge. AC-4 dit "evaluator déterministe, pas de LLM-as-judge" en offline → **les 4 métriques Ragas root ne sont PAS calculées en offline**. Seules les métriques déterministes (`keyword_overlap_rate`, `context_recall_structural`) alimentent `breakdown_by_mode.canon` et la gate B offline. Le champ `metrics` racine en offline = nulls explicites (`{faithfulness: null, ...}`) pour conserver le schéma AC-5.
3. **Stub LLM offline n'utilise pas `/v1/generate`** : AC-4 spécifie une réponse construite from `expected_answer_keywords + retrieved_chunks` — le runner court-circuite l'endpoint generate et construit la réponse en local. `/v1/retrieve` reste appelé réellement.

### (c) Zones d'incertitude — RÉSOLUES (humain, 2026-05-13)

- **OQ-1 `specs/golden_qa.jsonl` gitignored vs CI** → résolu : option (a) fixture CI dédié.
  - `specs/golden_qa.jsonl` reste gitignored = source de vérité humaine 46 entrées (spoilers, repo public à venir).
  - Nouveau fichier committé : `eval/fixtures/ci_smoke_qa.jsonl` — 8-12 entrées sanitisées, mêmes 4 modes, schéma identique, contextes/keywords non-spoilers (ex : `intro_p01`, `meta_p02`).
  - Runner expose `--set <path>` (déjà prévu), défaut `specs/golden_qa.jsonl`.
  - Workflow CI `pull_request` passe `--set eval/fixtures/ci_smoke_qa.jsonl`. `workflow_dispatch live` utilise défaut (vrai golden seeded localement).
  - CI valide mécanique runner + gate B offline structurel sans leak. Régression CI = bug code, pas trou data.
- **OQ-2 AC-17 ciblage commit gate-B-skip** → résolu : `${{ github.event.pull_request.head.sha }}` explicite.
  - `actions/checkout@v4` avec `ref: ${{ github.event.pull_request.head.sha }}` + `fetch-depth: 2` (parent requis pour diff).
  - Inspection : `git show -s --format=%s <sha>` (message) + `git diff --name-only <sha>^ <sha>` (files).
  - Unambigu, indépendant du merge ref synthétique.
- **OQ-3 Ragas en `--mode offline`** → résolu : option (i) Ragas root metrics = `null` en offline, gate B offline uniquement déterministes.
  - Offline `metrics: {faithfulness: null, answer_relevancy: null, context_precision: null, context_recall: null}`.
  - Les 4 comparaisons Ragas de gate B (AC-11 phrase 1) skipped si `baseline.<m>` OU `run.<m>` est `null` → log `event=gate_b_metric_skipped reason=null metric=<name>`.
  - Seuls `context_recall_structural ≥ baseline - 0.05` et `keyword_overlap_rate ≥ baseline - 0.05` (AC-11 phrase 2) sont enforcés en offline.
  - Pas de `NonLLMContextRecall` Ragas — redondant avec `context_recall_structural` déjà spec'd.

Notes architect mineures (non bloquantes) :
- `RAGAS_JUDGE_PROVIDER` (AC-14) : Ragas 0.2 attend `LangchainLLMWrapper(ChatOpenAI|ChatAnthropic|...)`. Runner instancie son propre client (dep `langchain-openai` déjà présente). Pas de réutilisation du worker-side `services/llm.py`.
- AC-17 regex : implémentation = `re.match(r"^chore\(eval\): bump baseline$", first_line, re.IGNORECASE)`. Strict, sans trim.

---

## Goal
Livrer un runner CLI `eval/ragas_runner.py` qui charge `specs/golden_qa.jsonl` (46 entrées, 4 modes), exécute le pipeline RAG (live ou offline), calcule métriques Ragas (canon, live) + métriques déterministes (4 modes), applique gate A absolue (live) + gate B no-regression (deux modes), produit `eval/runs/<ts>.json`, et bloque la PR via workflow `eval.yml` (offline) + workflow_dispatch (live).

## Acceptance criteria recap
Voir `specs/acceptance/EVAL-001.md` AC-1..AC-17 (verbatim, non recopié — limite ≤ 100 lignes).

## Files to touch

### Nouveaux
- `eval/loader.py` — `GoldenEntry(BaseModel)` (`extra="forbid"`), `load_golden_set(path) -> list[GoldenEntry]` ; erreur cite `id` + champ (AC-1)
- `eval/clients.py` — `RetrieveClient(base_url, timeout=60).search(query, request_id) -> RetrieveResponse` ; `GenerateClient(base_url, timeout=60).generate(query, request_id) -> GenerateResponse` ; mapping erreurs `timeout|upstream_error|malformed` (AC-3, AC-15)
- `eval/stub_llm.py` — `build_stub_answer(keywords: list[str], chunks: list[RetrievedChunk]) -> str` règle figée AC-4 ; pas d'appel réseau
- `eval/metrics.py` — `compute_keyword_overlap(answer, keywords) -> bool` ; `compute_context_recall_structural(expected, retrieved) -> float` ; `compute_ragas_metrics(entries_canon) -> dict[str,float]` (live only, via `ragas.evaluate`) ; `aggregate_breakdown(entries_by_mode) -> dict`
- `eval/gates.py` — `GateAResult`, `GateBResult` dataclasses ; `apply_gate_a(metrics) -> GateAResult` (AC-10) ; `apply_gate_b(current, baseline, runner_mode) -> GateBResult` (AC-11) ; reporting stderr formaté
- `eval/run_writer.py` — `RunFile(BaseModel)` schéma AC-5 (`mode_runner`, `totals`, `breakdown_by_mode`, `metrics`, `entries`) ; `write_run(path, run)` ; redaction (AC-16) via deny-list sur `LLM_API_KEY|DATABASE_URL|WORKERS_URL` env values
- `eval/baseline_skip.py` — `should_skip_gate_b(repo_path) -> bool` lecture `git log -1 --format=%s` + `git diff --name-only HEAD~1 HEAD` (AC-17)
- `eval/tests/test_loader.py` — AC-1 (id manquant, mode invalide, expected_contexts non-list, keyword non-list)
- `eval/tests/test_stub_llm.py` — AC-4 règle figée byte-for-byte + double-run hash SHA-256 identique
- `eval/tests/test_metrics.py` — AC-7 (overlap case-insensitive substring), AC-8 (recall structurel 0.5)
- `eval/tests/test_gates.py` — AC-10 (live seul), AC-11 (drops dans/hors tolérance, offline property-checks), AC-12 (exit code 0 + summary)
- `eval/tests/test_runner_cli.py` — AC-2 (mode manquant exit 2), AC-9 (baseline absent → auto-create + PASS), AC-15 (taux erreurs > 10%), AC-16 (no secret leak via env injection)
- `eval/tests/test_baseline_skip.py` — AC-17 cas (a)(b)(c) via `subprocess` git fixture
- `eval/tests/fixtures/golden_valid.jsonl`, `golden_invalid_id.jsonl`, `golden_invalid_mode.jsonl`, `run_canon_only.json`, `run_mixed.json`, `baseline_low.json`
- `eval/fixtures/ci_smoke_qa.jsonl` — 8-12 entrées sanitisées (4 modes, non-spoilers, contextes type `intro_p01`/`meta_p02`) ; committé en clair, utilisé par workflow CI offline (OQ-1 résolu)
- `eval/README.md` — usage CLI, env vars (`LLM_PROVIDER`, `RAGAS_JUDGE_PROVIDER`, `LLM_API_KEY`, `WORKERS_URL`), coût estimé ~$0.01/sample live (AC-14)

### Modifiés
- `eval/ragas_runner.py` — remplace skeleton : CLI `--mode {live,offline}` (required), `--set` (default `specs/golden_qa.jsonl`), `--baseline`, `--output` (default `eval/runs/<ts>.json`) ; orchestration loader → clients → metrics → gates → writer ; logs `structlog` JSON (`event=eval_start|eval_entry|eval_summary|eval_error`) ; exit codes : 0 OK, 1 gate/erreurs, 2 schema/CLI, 3 workers unreachable
- `.github/workflows/eval.yml` — réécrit : job `offline` sur `pull_request` (target main) avec `actions/checkout@v4` `ref: ${{ github.event.pull_request.head.sha }}` + `fetch-depth: 2` ; seed corpus + start workers + `python eval/ragas_runner.py --mode offline --set eval/fixtures/ci_smoke_qa.jsonl --baseline eval/baseline.json --output eval/runs/pr.json` + `actions/upload-artifact@v4` ; job `live` sur `workflow_dispatch` avec input `baseline` (optionnel) + secrets `LLM_API_KEY`, `OPENAI_API_KEY` (judge) ; step `baseline-skip-check` lit `git show -s --format=%s ${{ github.event.pull_request.head.sha }}` + `git diff --name-only <sha>^ <sha>` (AC-17, OQ-2 résolu)
- `workers/pyproject.toml` — confirmer `ragas>=0.2`, `datasets>=3.2` déjà présents (extra `dev`) ; ajouter `pytest-httpserver>=1.1` pour AC-3 si absent
- `CHANGELOG.md` — `[Unreleased] Added: **EVAL-001** : Ragas runner golden_qa (46 entrées 4 modes), gates A/B (absolue live + no-regression bidir), workflow CI offline + dispatch live, baseline.json versionné humain-only.`

### Explicitement non touchés (humain-only)
- `specs/golden_qa.jsonl` — figé à 46 entrées (AC précisé)
- `specs/properties.md` — aucun nouvel INV (eval = mesure, pas invariant runtime)
- `specs/openapi/gateway-to-workers.yml` — pas de changement contrat
- `migrations/*.sql` — aucune mutation DB
- `eval/baseline.json` — modifié uniquement par commit humain `chore(eval): bump baseline` (AC-17)

## Test strategy
- **Unit** : loader (Pydantic strict) ; stub_llm (déterminisme byte-identique double-run) ; metrics (overlap, recall structurel) ; gates (matrice tolérance/violation) ; baseline_skip (regex commit + diff)
- **Integration CLI** : invoquer `python eval/ragas_runner.py` via `subprocess`, asserter exit codes + stdout `event=eval_summary` + stderr formaté seuils
- **Integration end-to-end offline** : `pytest-httpserver` mock `/v1/retrieve` (échantillon 5 entrées golden), assert run.json shape AC-5, gate B passe
- **Property** : double exécution `--mode offline` → SHA-256 byte-identique modulo `started_at`/`finished_at`/`git_sha` (AC-4)
- **Property secret** : injection 100 runs `LLM_API_KEY=sk-xxxxx DATABASE_URL=postgres://u:pwd@h/db`, scan run.json + captured stdout/stderr → 0 match (AC-16)
- **Contract** : `actionlint .github/workflows/eval.yml` (AC-13/14)
- **Eval** : ce ticket EST le runner Ragas — pas de gate amont
- **Aucun INV property-based** : eval = boucle de mesure offline, pas de runtime invariant à proptester (`specs/properties.md` inchangé)

## Implementation steps (ordered)
1. `eval/loader.py` + `test_loader.py` (Pydantic `extra=forbid`, message d'erreur cite `id` + champ). Vert AC-1.
2. `eval/stub_llm.py` + `test_stub_llm.py` (règle AC-4 byte-for-byte, no I/O).
3. `eval/metrics.py` + `test_metrics.py` (keyword_overlap_rate, context_recall_structural ; Ragas wrappé derrière `compute_ragas_metrics` non appelé en offline).
4. `eval/gates.py` + `test_gates.py` (matrice live/offline × baseline/no-baseline × violation/tolerance).
5. `eval/clients.py` (httpx sync, timeout 60 s, `X-Request-Id` header, mapping `timeout|upstream_error|malformed`).
6. `eval/run_writer.py` (schéma Pydantic AC-5 + redaction secrets AC-16).
7. `eval/baseline_skip.py` (subprocess git inspecte `${PR_HEAD_SHA}` via env var injecté par workflow, regex strict `^chore\(eval\): bump baseline$` re.IGNORECASE ; diff `<sha>^..<sha>` doit contenir uniquement `eval/baseline.json`).
8. `eval/ragas_runner.py` (CLI argparse, orchestration, logs structlog, exit codes 0/1/2/3) + `test_runner_cli.py`.
9. `.github/workflows/eval.yml` (offline PR + dispatch live + step `baseline-skip-check`).
10. `eval/README.md` (usage + coût + env vars).
11. `CHANGELOG.md` `[Unreleased]/Added`.
12. Mesurer diff LOC. **Si > 300 LOC hors tests + fixtures → STOP, ouvrir split `EVAL-001a` / `EVAL-001b` (cf hypothèse 1).**

## Risks
- Si Ragas 0.2 API a évolué (`LangchainLLMWrapper`, `evaluate()` signature) → blocker `no-workaround.md` : appliquer protocole, écrire `docs/blockers.md`, ne pas patch around.
- `pytest-httpserver` non listé dans `pyproject.toml` `[dev]` actuel → ajout requis (≈ 0 LOC impact mais dep nouvelle, ADR non requis car < 1k LOC).
- LOC réel proche du seuil 300 — split EVAL-001a/b reste l'issue de secours documentée.
- Fixture CI `ci_smoke_qa.jsonl` doit rester représentatif (4 modes, schéma identique) sinon gate B offline donne faux positifs. Owner humain pour rafraîchir si schéma golden évolue.

## Post-review notes (review pass 2, 2026-05-13)

- **HIGH-A (CI workflow)**: Workflow now applies migrations via `bash migrations/run.sh` (official
  runner, FOUND-002) before the seed step; `DATABASE_URL` exported at seed step; workers boot with
  `LLM_PROVIDER=mistral LLM_MODEL=mistral-small-latest LLM_API_KEY=ci-placeholder` (offline eval
  calls `/v1/retrieve` only — no real LLM call happens).
- **HIGH-B (keyword_overlap_rate offline)**: Seed now uses lore-dummy narrative texts (keywords
  embedded in narrative context) and hash-based pseudo-embeddings (SHA-256 of source_path,
  1024-dim L2-normalised, deterministic). `keyword_overlap_rate` offline is documented as a
  plumbing/integration check (seed→DB→retrieve pipeline), not semantic quality. Semantic quality
  is gated via Ragas metrics in live mode. Future ticket EVAL-002 will introduce real bge-m3
  embeddings for meaningful offline retrieval. See `eval/README.md` for full rationale.
- **MED (redaction property test)**: Sentinels injected into `entry.answer` and `entry.citations`
  (serialised fields). Test now asserts `[REDACTED]` is present (positive assertion) — if
  `_redact_raw` were disabled the test would fail with the sentinel still in output.

## Out of scope
- LLM-as-judge custom (eval-rubric maison)
- Gates qualité sur `off_topic`, `lore_gap`, `mystery` (reporté EVAL-* aval)
- Eval multi-turn / conversationnel
- Auto-bump du baseline (humain explicite uniquement)
- Instrumentation Langfuse / OTel depuis le runner (OBS-* dédiés)
- Évaluation `citations` alignées vs `expected_contexts` au-delà du `context_recall_structural`
- Gates `latency_ms` / `cost_eur` (SEC-* / OBS-*)
- Retry auto sur entrée en erreur
- Mutation `specs/golden_qa.jsonl` (figé 46 entrées)
- Mutation `SYSTEM_PROMPT` (reporté GEN-003)
- Parallélisation des appels (séquentiel phase 1)
