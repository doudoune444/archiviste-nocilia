# Plan — EVAL-011 : juge Ragas Anthropic (Claude) + découplage embeddings

## Objectif

Débloquer le job Cloud Run `archiviste-eval` (timeout 7200 s) en remplaçant le juge Ragas Mistral gratuit (série, ~2,2 h) par Claude via clé API Anthropic + concurrence remontée → run en minutes → ligne `eval_runs` persistée → `/observability` affiche les métriques.

## Pre-flight

**Fichiers/dirs lus** : `eval/metrics.py`, `eval/ragas_runner.py`, `eval/persist.py`, `eval/clients.py` (survol), `eval/tests/test_ragas_judge.py`, `eval/pyproject.toml`, `eval/README.md`, `infra/terraform/eval_job.tf`, `infra/terraform/secrets.tf`, `infra/terraform/iam.tf`, `migrations/0005_eval_runs.sql` (via synthèse), `gateway/src/handlers/quality.rs` (via synthèse), `gateway/static/assets/observability.js` (via synthèse), `specs/acceptance/EVAL-003.md`, `specs/golden_qa.jsonl` (compté : 46 / 35 canon), `workers/src/archiviste_workers/services/llm.py`, `workers/uv.lock` (via synthèse).

**3 hypothèses clés** :
1. `--persist` (`ragas_runner.py:513`) s'exécute **avant** `apply_gate_a` (`:517`) → la ligne `eval_runs` est écrite quel que soit le verdict Gate A → page remplie même si Claude score < 0.85.
2. Ragas `LangchainLLMWrapper` accepte tout chat-model LangChain ; `ChatAnthropic(model=…, api_key=SecretStr)` est le pattern déjà prouvé dans `workers/.../llm.py:93`.
3. La SA `archiviste-runtime` a `roles/secretmanager.secretAccessor` **projet-wide** (`iam.tf:40-53`) → nouveau secret accessible sans binding par-secret (comme `mistral_api_key`).

**Zones d'incertitude** :
- Attribut exact du modèle sur `ChatAnthropic` (`.model` supposé) — vérifié au test, ajusté si besoin.
- Débit embeddings Mistral gratuit à concurrence 4 — `RAGAS_MAX_WORKERS` ajustable live sans rebuild ; bascule embeddings → openai en une env var si 429.
- Set golden GCS = repo (46/35) confirmé par l'opérateur.

## Design code (`eval/metrics.py`)

```
DEFAULT_ANTHROPIC_JUDGE_MODEL = "claude-haiku-4-5-20251001"
DEFAULT_OPENAI_EMBEDDINGS_MODEL = "text-embedding-3-small"
DEFAULT_JUDGE_EMBEDDINGS_PROVIDER = "mistral"

_build_ragas_judge_with_identity():
    provider = env RAGAS_JUDGE_PROVIDER (def mistral)
    api_key  = SecretStr(env LLM_API_KEY)
    mistral / openai : inchangés (chat+embeddings couplés, clé partagée)
    anthropic        : _build_anthropic_judge(api_key)
    else             : ValueError received=… allowed=mistral|openai|anthropic

_build_anthropic_judge(chat_key):
    chat  = ChatAnthropic(model=env RAGAS_JUDGE_MODEL|default pinné, api_key=chat_key)
    emb   = _build_judge_embeddings()          # découplé
    return LangchainLLMWrapper(chat), emb, chat_model_id

_build_judge_embeddings():
    provider = env RAGAS_JUDGE_EMBEDDINGS_PROVIDER (def mistral)
    key      = SecretStr(env RAGAS_JUDGE_EMBEDDINGS_API_KEY)
    model    = env RAGAS_JUDGE_EMBEDDINGS_MODEL | default selon provider
    mistral  : MistralAIEmbeddings ; openai : OpenAIEmbeddings ; else ValueError
    return LangchainEmbeddingsWrapper(emb)
```

Imports lazy (`PLC0415`) comme l'existant. Branches mistral/openai non touchées (back-compat, tests EVAL-003 verts).

## Contrat env vars (job Cloud Run)

| Var | Valeur |
|---|---|
| `RAGAS_JUDGE_PROVIDER` | `anthropic` |
| `RAGAS_JUDGE_MODEL` | (unset → `claude-haiku-4-5-20251001`) |
| `LLM_API_KEY` | secret `ANTHROPIC_API_KEY` |
| `RAGAS_JUDGE_EMBEDDINGS_PROVIDER` | `mistral` |
| `RAGAS_JUDGE_EMBEDDINGS_API_KEY` | secret `MISTRAL_API_KEY` |
| `RAGAS_JUDGE_EMBEDDINGS_MODEL` | (unset → `mistral-embed`) |
| `RAGAS_MAX_WORKERS` | `4` |

## Files to touch

- `eval/metrics.py` — branche anthropic + helper embeddings + allowlist.
- `eval/tests/test_ragas_judge.py` — 2 tests unknown→`cohere` ; +tests anthropic.
- `eval/pyproject.toml` — `langchain-anthropic>=0.2` (live+dev) + override mypy.
- `eval/uv.lock` — `uv lock`.
- `infra/terraform/secrets.tf` — secret `anthropic_api_key`.
- `infra/terraform/eval_job.tf` — env juge anthropic + concurrence 4.
- `eval/README.md` — doc.
- `CHANGELOG.md` — `## [Unreleased]` EVAL-011.
- `specs/acceptance/EVAL-011.md` — spec (humain-approuvé).

## TDD order

1. Spec EVAL-011 (fait). 2. Tests anthropic (échouent). 3. Impl metrics.py. 4. pyproject + relock. 5. Terraform. 6. README + CHANGELOG. 7. `ruff` + `mypy --strict` + `pytest`.

## Séquence déploiement (opérateur, hors PR)

1. Clé API sur console.anthropic.com. 2. `terraform apply` (secret + env). 3. `gcloud secrets versions add ANTHROPIC_API_KEY --data-file=-`. 4. Merge → rebuild `eval:latest`. 5. `gcloud run jobs execute archiviste-eval --region=europe-west9`. 6. Vérifier `/observability`.

## Risques

- Gate A peut échouer (juge ≠) sans bloquer l'affichage (persist avant gate) ; re-bake baseline = follow-up humain.
- 429 embeddings → bascule openai via env, sans rebuild.
