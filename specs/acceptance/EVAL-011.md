# EVAL-011 — Juge Ragas Anthropic (Claude) + découplage embeddings

## Contexte

Le job Cloud Run `archiviste-eval` ne termine jamais : avec `RAGAS_MAX_WORKERS=1` (anti-429 sur l'API Mistral gratuite, EVAL-009) le juge Ragas s'exécute en série à ~56 s/unité. Le golden set compte 46 entrées dont 35 `canon`, et Ragas calcule 4 métriques par entrée canon → 35×4 = 140 unités jugées ≈ 2,2 h, au-delà du plafond Cloud Run de 7200 s. Conséquence : `eval_runs` n'a aucune ligne `runner_mode='live'`, donc `GET /v1/quality` renvoie `{"status":"no_data"}` et la page `/observability` affiche « Aucune évaluation disponible. ».

Ce ticket débloque le run en branchant un juge chat **Anthropic (Claude)** via clé API (plateforme dev Anthropic), nettement plus rapide et avec des limites de débit permettant de remonter la concurrence. Comme Anthropic ne fournit pas d'API d'embeddings (requise par `AnswerRelevancy` / metrics Ragas), la moitié embeddings du juge est **découplée** de la moitié chat.

Ce ticket supersede délibérément EVAL-003 AC-1 / AC-4 : l'ensemble autorisé de `RAGAS_JUDGE_PROVIDER` passe de `{mistral, openai}` à `{mistral, openai, anthropic}`. EVAL-003 reste la spec d'origine du juge configurable, désormais étendue (même schéma de supersession que EVAL-003 vis-à-vis de EVAL-001 AC-14). `specs/acceptance/EVAL-003.md` n'est pas modifié.

## Acceptance criteria

- AC-1 : `eval/metrics.py` accepte `RAGAS_JUDGE_PROVIDER ∈ {mistral, openai, anthropic}` (défaut `mistral` en l'absence de la variable, inchangé) et retourne le couple `(llm, embeddings)` à passer à `ragas.evaluate(llm=…, embeddings=…)`.
- AC-2 : Pour `RAGAS_JUDGE_PROVIDER=anthropic`, la fonction construit un `ragas.llms.LangchainLLMWrapper` enveloppant un `langchain_anthropic.ChatAnthropic`. Le modèle chat par défaut est le snapshot daté pinné `claude-haiku-4-5-20251001` (anti-dérive, cohérent EVAL-003 AC-6), surchargeable via `RAGAS_JUDGE_MODEL`.
- AC-3 : Pour `RAGAS_JUDGE_PROVIDER=anthropic`, la moitié embeddings est découplée : provider lu depuis `RAGAS_JUDGE_EMBEDDINGS_PROVIDER` (valeurs `mistral` | `openai`, défaut `mistral`), clé depuis `RAGAS_JUDGE_EMBEDDINGS_API_KEY`, modèle depuis `RAGAS_JUDGE_EMBEDDINGS_MODEL` (défaut `mistral-embed` pour mistral, `text-embedding-3-small` pour openai). Les embeddings sont enveloppés dans `ragas.embeddings.LangchainEmbeddingsWrapper`.
- AC-4 : Les branches `mistral` et `openai` conservent leur comportement EVAL-003 (chat + embeddings du même provider, clé `LLM_API_KEY` partagée) — aucune régression.
- AC-5 : Une valeur de `RAGAS_JUDGE_PROVIDER` hors `{mistral, openai, anthropic}` lève une erreur explicite citant la valeur reçue et l'ensemble autorisé `mistral|openai|anthropic` ; aucun appel `ragas.evaluate()`. De même, une valeur de `RAGAS_JUDGE_EMBEDDINGS_PROVIDER` hors `{mistral, openai}` lève une erreur explicite.
- AC-6 : La clé chat est lue depuis `LLM_API_KEY` et la clé embeddings depuis `RAGAS_JUDGE_EMBEDDINGS_API_KEY`, toutes deux via `pydantic.SecretStr` ; aucune n'apparaît dans les logs, le fichier de run, ni l'artefact CI (cohérent EVAL-003 AC-7).
- AC-7 : Le champ d'identité du juge dans le fichier de run JSON enregistre le provider effectif (`anthropic`) et l'id de modèle chat résolu (défaut pinné ou override).
- AC-8 : `eval/pyproject.toml` ajoute `langchain-anthropic>=0.2` aux extras `live` et `dev`, `eval/uv.lock` est régénéré, et l'override mypy couvre `langchain_anthropic`.
- AC-9 : Le job Cloud Run `archiviste-eval` (`infra/terraform/eval_job.tf`) est reconfiguré : `RAGAS_JUDGE_PROVIDER=anthropic`, `LLM_API_KEY` ← secret `ANTHROPIC_API_KEY`, `RAGAS_JUDGE_EMBEDDINGS_PROVIDER=mistral`, `RAGAS_JUDGE_EMBEDDINGS_API_KEY` ← secret `MISTRAL_API_KEY`, `RAGAS_MAX_WORKERS=4`. Un nouveau secret `google_secret_manager_secret "anthropic_api_key"` (`infra/terraform/secrets.tf`) est ajouté — sans binding IAM par-secret (la SA `archiviste-runtime` détient déjà `roles/secretmanager.secretAccessor` projet-wide, `iam.tf`).
- AC-10 : `eval/README.md` documente le provider `anthropic`, le snapshot chat pinné, les env vars `RAGAS_JUDGE_EMBEDDINGS_PROVIDER` / `RAGAS_JUDGE_EMBEDDINGS_API_KEY`, et la note de supersession EVAL-003 AC-1/AC-4.

## Non-goals

- Pas de re-bake de `eval/baseline.json` ni de recalibration des seuils Gate A : changer de juge (Mistral → Claude) change les scores. Le premier run live Claude, le re-bake baseline et la recalibration Gate A sont un follow-up humain séparé (nécessite un run live payant réel). « Marche » ici = le runner s'exécute de bout en bout sous le plafond et persiste une ligne `eval_runs`, PAS « passe Gate A 0.85 ».
- Pas de provisionnement de la valeur du secret `ANTHROPIC_API_KEY` : action opérateur post-apply (`gcloud secrets versions add`, cf runbook bootstrap), hors code.
- Pas de nouveau binding IAM : `roles/secretmanager.secretAccessor` projet-wide existant couvre le nouveau secret.
- Pas de changement du chemin de génération (`/v1/generate`, `LLM_PROVIDER`) ni du prompt système — seul le juge Ragas est touché.
- Pas d'appel réseau / run live en CI — `ragas.evaluate()` live reste hors-CI.
- Pas de modification de `specs/acceptance/EVAL-003.md` (supersédé par référence).
- Pas d'embeddings auto-hébergés (Sentence-Transformers / BAAI/bge-m3) : conteneur eval 1 Gi, déféré V2.

## Pre-conditions

- EVAL-003 mergé : `RAGAS_JUDGE_PROVIDER`, `LLM_API_KEY`, `_build_ragas_judge_with_identity`, wrappers Ragas.
- OBS-009 mergé : job Cloud Run `archiviste-eval` (mode live + persist, déclenchement manuel).
- `langchain-anthropic>=0.2` disponible sur PyPI (registre officiel), compatible `ragas>=0.2` (`LangchainLLMWrapper`). Précédent : déjà dep workers (`langchain-anthropic==1.4.3`).
- Snapshot `claude-haiku-4-5-20251001` accessible via l'API Anthropic avec `ANTHROPIC_API_KEY`.

## Failure modes

- `RAGAS_JUDGE_PROVIDER` hors `{mistral, openai, anthropic}` → `ValueError` `received=<valeur> allowed=mistral|openai|anthropic`, aucun appel `ragas.evaluate()` (AC-5).
- `RAGAS_JUDGE_EMBEDDINGS_PROVIDER` hors `{mistral, openai}` → `ValueError` explicite (AC-5).
- `LLM_API_KEY` ou `RAGAS_JUDGE_EMBEDDINGS_API_KEY` absente en mode live anthropic → échec à la construction / au premier appel, message ne révélant aucun fragment de clé (AC-6).
- Snapshot Claude pinné indisponible (déprécié) → erreur API remontée au run live ; pinné = échec explicite reproductible, déclenche le follow-up humain de re-pin.
- 429 sur les embeddings Mistral gratuites à concurrence 4 → géré par le retry/backoff Ragas (`RunConfig`) ; bascule possible `RAGAS_JUDGE_EMBEDDINGS_PROVIDER=openai` via `gcloud run jobs update` sans rebuild.

## Touch points (informatif)

- `eval/metrics.py` — **modifié** : branche `anthropic` + helper embeddings découplé + allowlist élargie.
- `eval/tests/test_ragas_judge.py` — **modifié** : les 2 tests « unknown provider » utilisent une valeur tierce (`cohere`) ; nouveaux tests anthropic (type chat, modèle pinné défaut + override, embeddings découplées mistral & openai, clé non-fuitée).
- `eval/pyproject.toml` — **modifié** : `langchain-anthropic>=0.2` extras `live` + `dev` ; override mypy.
- `eval/uv.lock` — **régénéré**.
- `infra/terraform/secrets.tf` — **modifié** : secret `anthropic_api_key`.
- `infra/terraform/eval_job.tf` — **modifié** : env juge anthropic + concurrence 4.
- `eval/README.md` — **modifié** : doc provider anthropic + embeddings découplées.
- `CHANGELOG.md` — entrée `## [Unreleased]` section EVAL.

## Test oracle

- AC-1 : unit · `RAGAS_JUDGE_PROVIDER` non-défini → couple `mistral` (défaut inchangé).
- AC-2 : unit · `RAGAS_JUDGE_PROVIDER=anthropic` → `llm` est `LangchainLLMWrapper` enveloppant `ChatAnthropic` ; sans override le modèle résolu = `claude-haiku-4-5-20251001` ; avec `RAGAS_JUDGE_MODEL` → override reflété (inspection d'attribut, pas de réseau).
- AC-3 : unit · provider `anthropic` + `RAGAS_JUDGE_EMBEDDINGS_PROVIDER=mistral` → `embeddings` est `LangchainEmbeddingsWrapper` enveloppant `MistralAIEmbeddings` ; avec `=openai` → `OpenAIEmbeddings`.
- AC-4 : unit · `mistral` / `openai` inchangés (réutilise tests EVAL-003 existants).
- AC-5 : unit · `RAGAS_JUDGE_PROVIDER=cohere` → `ValueError` message contient `cohere` + `mistral|openai|anthropic` ; `RAGAS_JUDGE_EMBEDDINGS_PROVIDER=cohere` (avec chat anthropic) → `ValueError`.
- AC-6 : unit · `LLM_API_KEY` / `RAGAS_JUDGE_EMBEDDINGS_API_KEY` secrets → absentes de `repr()` / logs capturés.
- AC-7 : unit · `_run_ragas_evaluate` (ragas mocké) provider anthropic → `judge_identity = {provider: anthropic, chat_model: <résolu>}`.
- AC-8 : contract · `grep langchain-anthropic eval/pyproject.toml` présent dans `live` + `dev` ; `uv lock --check` (ou `uv sync --frozen`) passe.
- AC-9 : contract · `grep` Terraform `eval_job.tf` pour `anthropic`, `RAGAS_JUDGE_EMBEDDINGS_PROVIDER`, `RAGAS_MAX_WORKERS = "4"` ; `secrets.tf` pour `anthropic_api_key`.
- AC-10 : contract · `grep` README pour `anthropic` + `RAGAS_JUDGE_EMBEDDINGS_PROVIDER` + note supersession.

## Performance / SLO

- Non gated. Construction d'objets sans I/O. Le run live (hors gate phase 1) devrait finir en minutes (Haiku + concurrence 4) vs ~2,2 h (Mistral concurrence 1), sous le plafond 7200 s.

## Security / trust boundary

- Clés via `pydantic.SecretStr` (`.claude/rules/security.md` A09) ; jamais sérialisées (AC-6).
- Pas de nouvelle surface réseau en CI (pas d'appel live sur PR).
- `langchain-anthropic` depuis PyPI officiel, locké en `eval/uv.lock` (A06/A08), `--frozen`/`--locked` au build image. Pas d'ADR (déjà dep workers, <1k LOC, pas de FFI — cohérent EVAL-003).
- Nouveau secret via Secret Manager (jamais en clair dans Terraform) ; accès via grant projet-wide existant (moindre privilège déjà en place).

## Observability

- Aucun log/métrique applicatif nouveau. L'identité du juge (provider + modèle chat résolu) reste enregistrée dans le fichier de run JSON (AC-7) ; la persistance DB (`eval_runs`) est inchangée (OBS-009).

## Effort estimate

S/M — une branche provider (~25 lignes) + un helper embeddings découplé (~20 lignes) + élargissement allowlist, tests unitaires déterministes, dep + relock, Terraform (secret + env, 0 IAM), doc + CHANGELOG. 0 migration, 0 OpenAPI, 0 changement chemin génération. Vertical slice < 300 LOC code.

## Open questions

- OQ-1 (confirmation à l'impl) — Attribut exact exposant le modèle sur `ChatAnthropic` (`.model`) à vérifier au test ; n'impacte aucun AC observable.
- OQ-2 (opérationnel) — Concurrence optimale (`RAGAS_MAX_WORKERS`) vs limites de débit Anthropic tier-1 et Mistral-embed gratuit : `4` proposé, ajustable live sans rebuild.

## Status

ready
