# EVAL-004 — Fix juge live CI vers Mistral + déférer honnêtement le re-bake baseline

## Contexte

EVAL-003 a câblé le juge Ragas Mistral par défaut (`eval/metrics.py:110`, `mistral-large-2411`), mais le job `live` de `.github/workflows/eval.yml` force encore `RAGAS_JUDGE_PROVIDER: openai` et exige une clé `OPENAI_API_KEY` absente du repo — donc `--mode live` en CI est en réalité non-opérable. De plus, le job `live` seed le corpus de smoke via `eval.seed_test_corpus` (pseudo-embeddings hash-based, pas de bge-m3 réel), donc un baseline figé depuis ce run mesurerait le retrieval contre des vecteurs sans signification — pire qu'aucun baseline. Ce ticket se réduit donc à un ticket d'intégrité + honnêteté : réparer le bug juge live, et documenter explicitement que le re-bake baseline + la recalibration Gate A sont **déférés** (pas faits ici).

## Acceptance criteria

- AC-1 : Le job `live` de `.github/workflows/eval.yml` déclare `RAGAS_JUDGE_PROVIDER: mistral` (explicite) dans l'environnement de l'étape `run eval live`, ne contient plus aucune occurrence de `RAGAS_JUDGE_PROVIDER: openai`, et ne référence plus `OPENAI_API_KEY` (ligne supprimée) — aucun chemin de code ne consomme cette variable une fois le juge en Mistral.
- AC-2 : `--mode live` en CI est opérable avec la seule clé Mistral `LLM_API_KEY` (aucune clé OpenAI requise) : le juge Ragas résout en Mistral (`mistral-large-2411`) via le wiring EVAL-003 (`eval/metrics.py:110` défaut `mistral` + l'env corrigé en AC-1), de sorte que l'étape `run eval live` exécute le pipeline de bout en bout sans dépendre d'un secret OpenAI absent.
- AC-3 : `eval/README.md` documente (a) que l'eval live est câblé et exécutable avec le juge Mistral, (b) que le re-bake de `eval/baseline.json` et la recalibration des seuils Gate A sont **déférés**, avec la raison explicite que le job `live` seed des pseudo-embeddings de smoke-corpus (un baseline-worthy run exige un futur seed CI à corpus réel + bge-m3 réel), et (c) nomme le ticket de suivi portant ce seed corpus réel + le bake du baseline.
- AC-4 : Ce ticket n'introduit aucune migration, aucun changement de `specs/openapi/gateway-to-workers.yml`, aucun changement de `specs/golden_qa.jsonl`, aucun changement d'un fichier `eval/*.py` (y compris **aucune** édition de seuil dans `eval/gates.py` — la recalibration est déférée), et aucun changement de `eval/baseline.json` (il reste le bootstrap all-zeros). Seuls `.github/workflows/eval.yml`, `eval/README.md`, `CHANGELOG.md`, et optionnellement un nouveau `docs/adr/NNNN-*.md` sont touchés.

## Non-goals

- Pas de run live payant exécuté dans ce ticket (le premier run live réel jugé Mistral est déféré).
- Pas de re-bake / bump de `eval/baseline.json` — il reste le bootstrap all-zeros (`git_sha: "initial"`, `metrics: null`) ; aucun commit `chore(eval): bump baseline`.
- Pas de recalibration des seuils Gate A (`GATE_A_THRESHOLDS` dans `eval/gates.py`) — déférée au ticket de suivi qui disposera d'un run baseline-worthy.
- Pas de modification des tolérances Gate B (`GATE_B_TOLERANCES`, `GATE_B_OFFLINE_TOLERANCES`).
- Pas de chemin prod live-eval : OBS-009 (Cloud Run Job d'eval prod) est **abandonné / parqué**, hors périmètre ; aucun realignement du juge prod, aucune infra Terraform.
- Pas de seed CI à corpus réel + bge-m3 réel — c'est précisément la pré-condition du futur run baseline-worthy, déférée.
- Pas de modification du chemin de génération (`/v1/generate`, `LLM_PROVIDER`), du juge EVAL-003, ni du prompt système.
- Pas de re-pin du snapshot juge Mistral (`mistral-large-2411`) — repris tel quel d'EVAL-003.

## Pre-conditions

- **EVAL-001 mergé** : runner live `workflow_dispatch`, job `live` dans `.github/workflows/eval.yml`, Gate A / Gate B, invariant `eval/baseline.json`.
- **EVAL-003 mergé** (`213e11c`) : juge Ragas configurable, défaut effectif `mistral` (`mistral-large-2411`) câblé dans `eval/metrics.py` ; `RAGAS_JUDGE_PROVIDER` opérationnel.
- **Clé `LLM_API_KEY` Mistral disponible** côté `workflow_dispatch` (secret repo déjà référencé par le job `live`, lignes 215 + 233) — suffit au juge Mistral, aucune clé OpenAI nécessaire après ce ticket.

## Failure modes

- `RAGAS_JUDGE_PROVIDER: openai` ou `OPENAI_API_KEY` encore présent dans le job `live` après ce ticket (AC-1 non respecté) → le grep de contrat échoue, la PR est invalide.
- Édition d'un seuil dans `eval/gates.py` ou bump de `eval/baseline.json` dans cette PR (AC-4 non respecté) → smuggling de scope déféré, la PR est rejetée en review.
- `eval/README.md` ne nomme pas le ticket de suivi ou n'énonce pas la raison pseudo-embeddings (AC-3 non respecté) → la déférence n'est pas honnête/traçable, la PR est invalide.
- `LLM_API_KEY` Mistral absente au déclenchement d'un futur run live → échec explicite du juge (EVAL-003 Failure modes) ; hors périmètre de ce ticket (ce ticket ne déclenche aucun run).

## Touch points (informatif, non contraignant pour l'architect)

- `.github/workflows/eval.yml` — **modifié** : étape `run eval live` du job `live` ; `RAGAS_JUDGE_PROVIDER: openai` → `mistral` (ligne ~236), suppression de `OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}` (ligne ~234). Aucune autre étape touchée.
- `eval/README.md` — **modifié** : note « live runnable avec juge Mistral », note de déférence (re-bake baseline + recalibration Gate A) avec raison pseudo-embeddings, référence au ticket de suivi.
- `CHANGELOG.md` — entrée `## [Unreleased]` section EVAL.
- `docs/adr/NNNN-*.md` — **optionnel** : ADR (prochain numéro disponible `0011`) actant la déférence du re-bake baseline + l'abandon d'OBS-009, format `docs/adr/0000-template.md`.
- **Non touchés** : `migrations/*.sql`, `specs/openapi/gateway-to-workers.yml`, `specs/golden_qa.jsonl`, tout `eval/*.py` (dont `eval/gates.py`), `eval/baseline.json`, `eval/fixtures/` (AC-4).

## Test oracle

- AC-1 : contract / grep · dans `.github/workflows/eval.yml`, le job `live` contient `RAGAS_JUDGE_PROVIDER: mistral`, ne contient aucune occurrence de `RAGAS_JUDGE_PROVIDER: openai`, ne contient aucune occurrence de `OPENAI_API_KEY` ; `actionlint` parse le fichier sans erreur.
- AC-2 : logique / contract · l'env de l'étape `run eval live` (post-AC-1) ne fournit que `LLM_API_KEY` (pas de clé OpenAI) et `RAGAS_JUDGE_PROVIDER: mistral` → par `eval/metrics.py:110` (défaut `mistral`) + `_build_mistral_judge`, le juge résout en `mistral-large-2411` sans appel ni secret OpenAI. Vérification par lecture conjointe du YAML corrigé et de `eval/metrics.py`.
- AC-3 : contract / grep · `eval/README.md` contient (a) une mention « live runnable / Mistral judge », (b) une note de déférence citant « pseudo-embeddings » et « baseline » + « Gate A recalibration », (c) l'ID du ticket de suivi.
- AC-4 : contract · `git diff --name-only` sur la PR ⊆ `{.github/workflows/eval.yml, eval/README.md, CHANGELOG.md, docs/adr/NNNN-*.md}` ; `git diff` sur `migrations/`, `specs/openapi/gateway-to-workers.yml`, `specs/golden_qa.jsonl`, `eval/*.py`, `eval/gates.py`, `eval/baseline.json`, `eval/fixtures/` → 0 changement.

## Security / trust boundary

- Suppression de `OPENAI_API_KEY` du job `live` : réduit la surface de secrets référencés en CI à la seule `LLM_API_KEY` Mistral (lue via `pydantic.SecretStr`, EVAL-001 AC-16 / EVAL-003 AC-7).
- Aucune nouvelle surface réseau ajoutée : le job `live` reste `workflow_dispatch`-only ; ce ticket ne déclenche aucun run.

## Observability

- Aucune observabilité custom ajoutée ; le logging structlog d'EVAL-001 reste inchangé.

## Effort estimate

XS — édition de 2 lignes dans `.github/workflows/eval.yml` (1 changée, 1 supprimée), mise à jour `eval/README.md` (note live + déférence + ticket de suivi), entrée CHANGELOG, ADR optionnel. 0 migration, 0 OpenAPI, 0 code, 0 changement golden set, 0 changement baseline. Diff < 60 lignes (dont l'ADR).

## Forward-pointer (travail déféré, non spécifié ici)

- **Re-bake baseline + recalibration Gate A** : déféré à **EVAL-002** (seed corpus réel + bge-m3, déjà nommé dans `eval/README.md:55`), dont c'est la suite directe. Pré-condition bloquante : un seed CI à corpus réel + bge-m3 réel produisant un run baseline-worthy. Tant que le job `live` seed des pseudo-embeddings, aucun baseline ne doit être figé.
- **Prod live-eval (OBS-009)** : parqué / abandonné, hors de toute PR à venir tant que la décision n'est pas ré-ouverte.

## Open questions

- Aucune. Toutes les décisions de périmètre sont tranchées (réduction à intégrité + déférence honnête).

## Status

ready
