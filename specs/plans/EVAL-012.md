# EVAL-012 — Plan

## Pre-flight

### (a) Fichiers/dirs lus
- `eval/ragas_runner.py` (`_run_entry_offline` L134-151, `_run_entry_live` L154-180).
- `eval/metrics.py` (`_run_ragas_evaluate` dataset L313-323).
- `eval/run_writer.py` (`EntryResult` L27-42 — `retrieved_chunk_texts` transient `repr=False`, non sérialisé L103-115).
- `eval/tests/test_ragas_judge.py` (`test_run_ragas_evaluate_passes_judge_to_ragas` L258-307 — modèle de capture `dataset`).

### (b) 3 hypothèses clés
1. Les chunks de `retrieve_response.chunks` (live) exposent `.text` et `.source_path` comme en offline (`_run_entry_offline` les lit déjà L145+L147). → même type de chunk.
2. `retrieved_chunk_texts` est transient (`repr=False`) et non listé dans `_build_run_dict` → écrire le texte n'altère pas le fichier de run (AC-4).
3. `compute_context_recall_structural` lit `retrieved_contexts` (chemins), pas `contexts` du dataset Ragas → conserver `retrieved_contexts` préserve le recall structural (AC-3).

### (c) Zones d'incertitude
- Aucune sur le code. Les vrais scores live ne se vérifient qu'au run Cloud Run (hors-CI, post-merge) — le test unitaire asservit le câblage, pas les scores.

## Files to touch
- `eval/ragas_runner.py` — `_run_entry_live` : `entry_result.retrieved_chunk_texts = [c.text for c in chunks]`.
- `eval/metrics.py` — `_run_ragas_evaluate` : dataset `"contexts": e.retrieved_chunk_texts`.
- `eval/run_writer.py` — commentaire `retrieved_chunk_texts` (overlap offline **+ contexts Ragas live**).
- `eval/tests/test_ragas_judge.py` — nouveau test `test_run_ragas_evaluate_contexts_are_chunk_texts_not_paths`.
- `specs/acceptance/EVAL-012.md`, `specs/plans/EVAL-012.md` — spec + plan.
- `CHANGELOG.md` — entrée `[Unreleased]` / Fixed.

## Order (vertical-slice)
1. Test rouge (capture `dataset`, assert `contexts` = textes).
2. Fix prod (3 spots) → vert.
3. CHANGELOG.
4. ruff + mypy --strict + pytest (venv eval).

## Verification avant dépense
Tout vert local AVANT de proposer un run Cloud Run live (coûteux). Le test prouve le câblage ; le run live prouvera les scores.
