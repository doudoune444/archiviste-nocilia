# EVAL-012 — Contextes Ragas en mode live : texte des chunks, pas les chemins

## Contexte

Bug pré-existant, révélé par EVAL-011 (le run live termine enfin et persiste une ligne `eval_runs`). Scores live faux : faithfulness≈0.013, context_precision=0.0, context_recall=0.0, answer_relevancy≈0.53. Cause : le harness passe à `ragas.evaluate()` les **chemins** des chunks (`source_path`) en guise de `contexts`, pas leur **texte**. Le juge évalue donc la réponse contre des chaînes de chemins de fichiers, d'où des scores quasi nuls.

- `eval/ragas_runner.py::_run_entry_live` remplit `retrieved_contexts` (chemins) mais jamais `retrieved_chunk_texts` (seul `_run_entry_offline` le fait).
- `eval/metrics.py::_run_ragas_evaluate` construit le dataset avec `"contexts": e.retrieved_contexts` → des chemins.

## Acceptance criteria

- AC-1 : en mode live, `_run_entry_live` remplit `retrieved_chunk_texts = [c.text for c in chunks]` (parité avec `_run_entry_offline`).
- AC-2 : `_run_ragas_evaluate` construit le dataset Ragas avec `"contexts"` = `e.retrieved_chunk_texts` (TEXTE des chunks), jamais les chemins.
- AC-3 : `retrieved_contexts` (chemins) conservé inchangé — `compute_context_recall_structural` (recall structural canon) en dépend.
- AC-4 : fichier de run JSON inchangé — `retrieved_chunk_texts` reste transient (`repr=False`, non sérialisé) ; seuls les chemins restent écrits sous `entries[].retrieved_contexts`.

## Non-goals

- Pas de re-bake `eval/baseline.json` ni recalibration Gate A (un vrai run live corrigé est requis d'abord — follow-up humain).
- Pas d'appel réseau / run live en CI.
- Pas de changement du chemin retrieval/génération ni du schéma du fichier de run (aucun nouveau champ sérialisé).

## Touch points (informatif)

- `eval/ragas_runner.py` — **modifié** : `_run_entry_live` remplit `retrieved_chunk_texts`.
- `eval/metrics.py` — **modifié** : dataset `"contexts"` ← `retrieved_chunk_texts`.
- `eval/run_writer.py` — **modifié** : commentaire `retrieved_chunk_texts` (couvre aussi les contexts Ragas live, plus seulement l'overlap offline).
- `eval/tests/test_ragas_judge.py` — **modifié** : test asservissant `contexts` = textes des chunks, pas chemins.
- `CHANGELOG.md` — entrée `## [Unreleased]` section EVAL.

## Test oracle

- AC-1+AC-2 : unit · `_run_ragas_evaluate` (ragas mocké, capture du `dataset`) avec une `EntryResult` où `retrieved_contexts=["doc/intro.md"]` et `retrieved_chunk_texts=["<texte réel>"]` → `dataset[0]["contexts"] == ["<texte réel>"]`, jamais `["doc/intro.md"]`.
- AC-3 : couvert par les tests existants de `compute_context_recall_structural` (inchangé).

## Effort estimate

XS — ~3 lignes prod + 1 commentaire + 1 test. Vertical slice « well under » 300 LOC.

## Status

ready
