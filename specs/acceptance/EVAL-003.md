# EVAL-003 — Juge Ragas configurable Mistral (wiring provider, défaut mistral pinné)

## Contexte

Le mode live de l'eval runner appelle aujourd'hui `ragas.evaluate()` sans passer de `llm=`/`embeddings=` explicites (`eval/metrics.py:106`), donc Ragas retombe silencieusement sur OpenAI ; l'env var `RAGAS_JUDGE_PROVIDER` documentée par EVAL-001 AC-14 (défaut `openai`) n'est jamais réellement câblée dans le code. Ce ticket réalise l'intention de EVAL-001 AC-14 (juge configurable) en branchant une fonction de sélection de provider, avec un défaut `mistral` pinné sur un snapshot daté pour que les scores Gate B ne dérivent pas silencieusement. Ce ticket supersede délibérément le défaut `openai` de EVAL-001 AC-14 : le défaut effectif devient `mistral`, EVAL-001 AC-14 restant la spec d'origine de l'intention « juge configurable » désormais concrétisée.

## Acceptance criteria

- AC-1 : `eval/metrics.py` expose une fonction de construction du juge qui, selon `RAGAS_JUDGE_PROVIDER` (valeurs `mistral` | `openai`, défaut `mistral` en l'absence de la variable), retourne le couple `(llm, embeddings)` à passer à `ragas.evaluate(llm=..., embeddings=...)`.
- AC-2 : Pour `RAGAS_JUDGE_PROVIDER=mistral` (ou non-défini), la fonction construit un `ragas.llms.LangchainLLMWrapper` enveloppant un `langchain_mistralai.ChatMistralAI`, et un `ragas.embeddings.LangchainEmbeddingsWrapper` enveloppant un `langchain_mistralai.MistralAIEmbeddings` (le wrapper embeddings est requis par ragas ≥0.4, cf OQ-3).
- AC-3 : Pour `RAGAS_JUDGE_PROVIDER=openai`, la fonction construit l'équivalent OpenAI (`langchain_openai.ChatOpenAI` enveloppé en `LangchainLLMWrapper` + `langchain_openai.OpenAIEmbeddings` enveloppé en `ragas.embeddings.LangchainEmbeddingsWrapper`).
- AC-4 : Une valeur de `RAGAS_JUDGE_PROVIDER` hors `{mistral, openai}` fait lever une erreur explicite citant la valeur reçue et l'ensemble des valeurs autorisées ; aucun appel `ragas.evaluate()` n'a lieu.
- AC-5 : `ragas.evaluate()` est invoqué avec les objets `llm=` et `embeddings=` produits par la fonction de sélection (le run live n'utilise plus le juge OpenAI implicite par défaut de Ragas).
- AC-6 : Le modèle chat du juge `mistral` est par défaut un snapshot daté pinné (ex. `mistral-large-2411`, id exact confirmé à l'impl), surchargeable via `RAGAS_JUDGE_MODEL` ; le modèle d'embeddings du juge `mistral` est `mistral-embed` (pas de snapshot daté existant, modèle stable), surchargeable via `RAGAS_JUDGE_EMBEDDINGS_MODEL`.
- AC-7 : La clé d'API du juge est lue depuis l'env var existante `LLM_API_KEY` via `pydantic.SecretStr`, et n'apparaît jamais dans les logs stdout/stderr, le fichier de run, ni l'artefact CI (cohérent EVAL-001 AC-16).
- AC-8 : `eval/pyproject.toml` ajoute `langchain-mistralai>=0.2` aux extras `live` et `dev`, et `eval/uv.lock` est régénéré en cohérence.
- AC-9 : `eval/README.md` documente le défaut effectif `mistral`, les env vars `RAGAS_JUDGE_PROVIDER` / `RAGAS_JUDGE_MODEL` / `RAGAS_JUDGE_EMBEDDINGS_MODEL`, le snapshot chat pinné par défaut, et note explicitement la supersession du défaut `openai` de EVAL-001 AC-14.
- AC-10 : Le fichier de run JSON (sortie du runner, extension additive de EVAL-001 AC-5) contient un champ d'identité du juge enregistrant le provider effectif et l'id de modèle chat résolu (après application du défaut / de l'override) ; pour un run `mistral` par défaut, le champ vaut le provider `mistral` et l'id du snapshot chat pinné effectivement utilisé.

## Non-goals

- Pas de premier run live Mistral ni de re-bake de `eval/baseline.json` dans ce ticket : « live works » ici = le runner s'exécute de bout en bout et produit des scores jugés par Mistral, PAS « passe la Gate A 0.85 ». Le premier run live payant, le re-bake du baseline et toute recalibration des seuils Gate A sont un follow-up humain séparé (nécessite un run live payant réel, ne peut pas vivre dans une PR de code).
- Pas de modification de `eval/gates.py` ni des seuils Gate A / Gate B — wiring du juge uniquement.
- Pas de changement du chemin de génération (`/v1/generate`, `LLM_PROVIDER`) ni du prompt système — seul le juge Ragas est touché.
- Pas d'appel réseau / run live en CI — `ragas.evaluate()` live reste `workflow_dispatch`-only (cohérent EVAL-001 AC-14).
- Pas de Terraform, pas de migration, pas de modification de `specs/openapi/gateway-to-workers.yml`.
- L'identité du juge (AC-10) est enregistrée dans le fichier de run JSON UNIQUEMENT — pas de colonne `eval_runs`, pas de migration, pas de persistance DB (déféré au follow-up de re-baseline humain).
- Pas de rebuild de l'image Cloud Run (OBS-008) dans cette PR — conséquence aval du changement de deps, hors périmètre code ici.
- Pas d'ADR : `langchain-mistralai` est déjà une dépendance workers, < 1k LOC, pas de FFI (cf `.claude/rules/security.md` A06).

## Pre-conditions

- EVAL-001 mergé : runner, `eval/metrics.py` avec `_run_ragas_evaluate`, env vars `RAGAS_JUDGE_PROVIDER` / `LLM_API_KEY`, mode live `workflow_dispatch`.
- OBS-008 mergé : image conteneur eval (le rebuild d'image consommera la nouvelle dep, hors ce ticket).
- `langchain-mistralai>=0.2` disponible sur PyPI (registre officiel) et compatible Ragas `>=0.2` (`LangchainLLMWrapper`).
- Snapshot Mistral daté `mistral-large-2411` (ou id confirmé à l'impl) accessible via l'API Mistral avec la clé `LLM_API_KEY`.

## Failure modes

- `RAGAS_JUDGE_PROVIDER` hors `{mistral, openai}` → erreur explicite levée (ex. `ValueError`) citant `received=<valeur> allowed=mistral|openai`, aucun appel `ragas.evaluate()` (AC-4).
- `LLM_API_KEY` absente en mode live → échec à la construction/au premier appel juge, message ne révélant aucun fragment de clé (AC-7).
- Snapshot Mistral pinné indisponible côté API (déprécié) → erreur API remontée au run live (mode dispatch) ; le défaut étant pinné, l'échec est explicite et reproductible plutôt que silencieux — déclenche le follow-up humain de re-pin.

## Touch points (informatif, non contraignant pour l'architect)

- `eval/metrics.py` — **modifié** : nouvelle fonction de sélection/construction du juge `(llm, embeddings)` selon `RAGAS_JUDGE_PROVIDER` + modèles via env ; câblage de `llm=`/`embeddings=` dans `_run_ragas_evaluate` → `ragas.evaluate()`.
- `eval/pyproject.toml` — **modifié** : `langchain-mistralai>=0.2` dans extras `live` + `dev`.
- `eval/uv.lock` — **régénéré** (`uv lock`).
- `eval/tests/` — **nouveau/modifié** : tests unitaires déterministes de la fonction de sélection (pas d'appel réseau).
- `eval/README.md` — **modifié** : défaut `mistral`, env vars judge, snapshot pinné, note supersession EVAL-001 AC-14.
- `CHANGELOG.md` — entrée `## [Unreleased]` section EVAL.

## Test oracle

- AC-1 : unit · `RAGAS_JUDGE_PROVIDER` non-défini → la fonction retourne le couple `(llm, embeddings)` du provider `mistral` (défaut).
- AC-2 : unit · `RAGAS_JUDGE_PROVIDER=mistral` → assert `llm` est un `LangchainLLMWrapper` enveloppant un `ChatMistralAI` et `embeddings` un `MistralAIEmbeddings` (inspection de type/attribut, pas de réseau).
- AC-3 : unit · `RAGAS_JUDGE_PROVIDER=openai` → assert le couple OpenAI équivalent (`ChatOpenAI` wrappé + `OpenAIEmbeddings`).
- AC-4 : unit · `RAGAS_JUDGE_PROVIDER=anthropic` (ou autre) → assert l'erreur levée + message contient la valeur reçue et `mistral|openai`.
- AC-5 : unit · `ragas.evaluate` monkeypatché/mocké → assert qu'il est appelé avec `llm=` et `embeddings=` égaux aux objets produits par la fonction de sélection (aucun appel réseau réel).
- AC-6 : unit · sans override → `ChatMistralAI` construit avec le snapshot daté pinné et `MistralAIEmbeddings` avec `mistral-embed` ; avec `RAGAS_JUDGE_MODEL` / `RAGAS_JUDGE_EMBEDDINGS_MODEL` définis → les modèles reflètent l'override (inspection d'attribut).
- AC-7 : unit/property · avec `LLM_API_KEY=sk-secret-xxx`, assert qu'aucune sous-chaîne `sk-secret-xxx` n'apparaît dans `repr()`/log de l'objet juge ni dans la sortie capturée.
- AC-8 : contract · `grep langchain-mistralai eval/pyproject.toml` présent dans `live` et `dev` ; `uv lock --check` (ou `uv sync --frozen`) passe.
- AC-9 : contract · `grep` README pour `mistral` défaut + `RAGAS_JUDGE_MODEL` + `RAGAS_JUDGE_EMBEDDINGS_MODEL` + mention supersession EVAL-001 AC-14.
- AC-10 : integration · run runner (avec `ragas.evaluate` mocké, sans réseau) en provider `mistral` par défaut → assert que le JSON de run produit contient le champ d'identité du juge avec provider `mistral` et l'id de modèle chat résolu attendu ; assert qu'aucune écriture DB / `eval_runs` n'est impliquée.

## Performance / SLO

- Non gated (la fonction de sélection est de la construction d'objets, sans I/O). Le coût/latence du run live dépend du provider et reste hors gate phase 1 (cf EVAL-001).

## Security / trust boundary

- `LLM_API_KEY` lue via `pydantic.SecretStr` (cf `.claude/rules/security.md` A09) ; jamais sérialisée dans run/logs/artefact (AC-7, conforme EVAL-001 AC-16).
- Aucune nouvelle surface réseau ajoutée en CI : pas d'appel live sur PR (`workflow_dispatch`-only).
- Dépendance `langchain-mistralai` depuis PyPI officiel, lockée en `eval/uv.lock` (A06/A08), `--frozen`/`--locked` au build image.

## Observability

- Aucun log/métrique applicatif nouveau requis ; le run live conserve le logging structlog de EVAL-001. L'identité du juge (provider + id de modèle chat résolu) est enregistrée dans le fichier de run JSON pour traçabilité/reproductibilité (AC-10), sans persistance DB (cf Non-goals).

## Effort estimate

S — une fonction de sélection (~30-50 lignes) + câblage `llm=`/`embeddings=` dans `_run_ragas_evaluate`, ajout d'une dep + relock, tests unitaires déterministes, doc README + CHANGELOG. 0 Terraform, 0 migration, 0 OpenAPI, 0 changement du chemin génération. Vertical slice < 300 LOC.

## Open questions

- OQ-1 (confirmation à l'impl) — Snapshot Mistral chat exact à pinner : `mistral-large-2411` est proposé comme défaut committé, mais l'id exact disponible/recommandé au moment de l'impl doit être confirmé (vérifier la doc Mistral à l'impl). Le choix n'impacte aucun AC observable (AC-6 fige « un snapshot daté », pas l'id précis) mais doit être tranché avant le commit du défaut.
- OQ-3 (confirmation à l'impl) — `langchain-mistralai>=0.2` expose-t-il bien `ChatMistralAI` ET `MistralAIEmbeddings` compatibles avec le `LangchainLLMWrapper` de `ragas>=0.2` (et Ragas accepte-t-il des embeddings LangChain bruts vs un wrapper Ragas dédié) ? À vérifier à l'impl ; si Ragas exige un wrapper embeddings spécifique (ex. `LangchainEmbeddingsWrapper`), AC-2/AC-3 doivent nommer ce wrapper. N'invalide pas le ticket mais précise la forme exacte du couple retourné.

## Status

ready
