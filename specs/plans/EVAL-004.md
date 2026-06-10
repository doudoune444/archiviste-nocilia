# Plan — EVAL-004 Fix live CI judge to Mistral + honestly defer baseline re-bake

## Goal
Make `--mode live` in CI operable with the Mistral judge alone (drop the dead OpenAI key + `openai` provider override), and document in `eval/README.md` + CHANGELOG that the baseline re-bake and Gate A recalibration are deferred to EVAL-002 because the live job seeds smoke-corpus pseudo-embeddings.

## Acceptance criteria recap
- AC-1 : Le job `live` de `.github/workflows/eval.yml` déclare `RAGAS_JUDGE_PROVIDER: mistral` (explicite) dans l'environnement de l'étape `run eval live`, ne contient plus aucune occurrence de `RAGAS_JUDGE_PROVIDER: openai`, et ne référence plus `OPENAI_API_KEY` (ligne supprimée) — aucun chemin de code ne consomme cette variable une fois le juge en Mistral.
- AC-2 : `--mode live` en CI est opérable avec la seule clé Mistral `LLM_API_KEY` (aucune clé OpenAI requise) : le juge Ragas résout en Mistral (`mistral-large-2411`) via le wiring EVAL-003 (`eval/metrics.py:110` défaut `mistral` + l'env corrigé en AC-1), de sorte que l'étape `run eval live` exécute le pipeline de bout en bout sans dépendre d'un secret OpenAI absent.
- AC-3 : `eval/README.md` documente (a) que l'eval live est câblé et exécutable avec le juge Mistral, (b) que le re-bake de `eval/baseline.json` et la recalibration des seuils Gate A sont **déférés**, avec la raison explicite que le job `live` seed des pseudo-embeddings de smoke-corpus (un baseline-worthy run exige un futur seed CI à corpus réel + bge-m3 réel), et (c) nomme le ticket de suivi portant ce seed corpus réel + le bake du baseline.
- AC-4 : Ce ticket n'introduit aucune migration, aucun changement de `specs/openapi/gateway-to-workers.yml`, aucun changement de `specs/golden_qa.jsonl`, aucun changement d'un fichier `eval/*.py` (y compris **aucune** édition de seuil dans `eval/gates.py`), et aucun changement de `eval/baseline.json`. Seuls `.github/workflows/eval.yml`, `eval/README.md`, `CHANGELOG.md`, et optionnellement un nouveau `docs/adr/NNNN-*.md` sont touchés.

## Files to touch
- `.github/workflows/eval.yml` — step `run eval live`: line 236 `RAGAS_JUDGE_PROVIDER: openai` → `mistral`; delete line 234 `OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}`. No other step touched.
- `eval/README.md` — amend "Estimated Cost" (line 59-62, drop OpenAI cost framing, state live runnable w/ Mistral) + add a deferral note (pseudo-embeddings → baseline re-bake + Gate A recalibration deferred to EVAL-002). Reuse existing EVAL-002 pointer (line 55).
- `CHANGELOG.md` — `## [Unreleased]` → `### Fixed` entry, EVAL-004.
- `docs/adr/0011-eval-004-defer-baseline-rebake.md` — RECOMMENDED (see Risks). Format = `docs/adr/0000-template.md`.

## Test strategy
- AC-1 (contract/grep): in `.github/workflows/eval.yml` — `grep "RAGAS_JUDGE_PROVIDER: mistral"` ≥1; `grep "RAGAS_JUDGE_PROVIDER: openai"` == 0; `grep "OPENAI_API_KEY"` == 0. `actionlint` parses clean (wired pre-commit `.pre-commit-config.yaml:5` + CI `ci.yml:30`).
- AC-2 (logic/read): joint read of corrected YAML (`LLM_API_KEY` only, `RAGAS_JUDGE_PROVIDER: mistral`) + `eval/metrics.py:110` default `mistral` + `_build_mistral_judge` → resolves `mistral-large-2411`, no OpenAI secret. No new test (no code change).
- AC-3 (grep): `eval/README.md` contains "Mistral" live-runnable mention + "pseudo-embeddings" + "baseline" + "Gate A" deferral wording + "EVAL-002".
- AC-4 (contract): `git diff --name-only main...HEAD` ⊆ `{.github/workflows/eval.yml, eval/README.md, CHANGELOG.md, docs/adr/0011-*.md}`; `git diff` on `migrations/`, `specs/openapi/`, `specs/golden_qa.jsonl`, `eval/*.py`, `eval/baseline.json`, `eval/fixtures/` → 0.
- No property test (`specs/properties.md` lists no relevant invariant). No schemathesis (OpenAPI untouched). No Ragas run (no RAG code path changed; this ticket triggers no run).

## Implementation steps (ordered)
1. Edit `.github/workflows/eval.yml`: delete OPENAI_API_KEY line (234), change provider `openai`→`mistral` (236). Run `actionlint`.
2. Edit `eval/README.md`: rewrite Estimated Cost section for Mistral + add deferral note citing pseudo-embeddings + EVAL-002.
3. Add `CHANGELOG.md` `### Fixed` EVAL-004 entry under `[Unreleased]`.
4. Write `docs/adr/0011-eval-004-defer-baseline-rebake.md` (recommended).
5. Verify scope with `git diff --name-only` against the allowlist (AC-4 oracle).

Commit grouping (spec mandates no multi-commit choreography — no baseline bump):
single commit `fix(eval): live CI judge to Mistral, defer baseline re-bake (EVAL-004)` covering all four files. ADR + README + CHANGELOG are one logical change with the YAML fix; no ordering constraint exists (Gate-B skip choreography does not apply, baseline untouched).

## Risks / open questions
- ADR decision — **RECOMMEND INCLUDE**. The ticket records two durable decisions: (a) deferring the baseline re-bake / Gate A recalibration with a stated precondition (real-corpus + bge-m3 seed), and (b) parking OBS-009 prod live-eval. These are architectural "why we did NOT act" records that future readers (esp. whoever picks up EVAL-002) need; an ADR is the canonical home, README is operational. Cost ≈ 20 lines, well within budget. Skip only if the human prefers README-only.
- `OPENAI_API_KEY` removal safety — **confirmed safe**: grepped all of `.github/workflows/`; the only occurrence is line 234 in the `run eval live` step. No other step or job reads it. Once the judge is `mistral`, `eval/metrics.py` never reads `OPENAI_API_KEY` (key comes from `LLM_API_KEY`). No breakage.
- `secrets.OPENAI_API_KEY` referenced but absent in repo today — removing the reference is strictly a reduction of secret surface (security.md A09 / spec §Security), no functional regression.

## Out of scope (AC-4 + Non-goals)
- No paid live run; no `eval/baseline.json` re-bake/bump (stays bootstrap all-zeros).
- No `GATE_A_THRESHOLDS` / `eval/gates.py` edit; no Gate B tolerance change.
- No `eval/*.py` change at all; no migration; no `specs/openapi`; no `specs/golden_qa.jsonl`; no `eval/fixtures/`.
- No real-corpus + bge-m3 CI seed (the EVAL-002 precondition).
- No OBS-009 prod live-eval realignment (parked); no `mistral-large-2411` re-pin; no generation/system-prompt change.

## LOC
~2 lines YAML, ~10 README, ~3 CHANGELOG, ~20 ADR ≈ <40 LOC. Well under 300.
