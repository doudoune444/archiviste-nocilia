# Eval Runner — Ragas + CI Gates

CLI tool measuring RAG pipeline quality against a golden Q/A set.

## Usage

```bash
# Offline mode (deterministic, no LLM-as-judge — used on PR CI)
python eval/ragas_runner.py --mode offline --set eval/fixtures/ci_smoke_qa.jsonl \
    --baseline eval/baseline.json --output eval/runs/latest.json

# Live mode (requires real workers + LLM — manual / workflow_dispatch)
python eval/ragas_runner.py --mode live --set specs/golden_qa.jsonl \
    --baseline eval/baseline.json --output eval/runs/latest.json
```

## Environment Variables

| Variable | Required | Description |
|---|---|---|
| `WORKERS_URL` | No (default `http://localhost:8000`) | Workers base URL |
| `LLM_PROVIDER` | Live only (default `mistral`) | Provider for `/v1/generate` calls |
| `RAGAS_JUDGE_PROVIDER` | Live only (default **`mistral`**) | LLM judge provider for Ragas metrics (`mistral`\|`openai`) |
| `RAGAS_JUDGE_MODEL` | No (default `mistral-large-2411`) | Chat model snapshot for the Ragas judge. Pinned dated snapshot prevents silent score drift. Override to `mistral-large-latest` etc. |
| `RAGAS_JUDGE_EMBEDDINGS_MODEL` | No (default `mistral-embed`) | Embeddings model for the Ragas judge. |
| `LLM_API_KEY` | Live only | API key for the LLM judge (Ragas). Read as `pydantic.SecretStr` — never logged or written to the run file. |

> **EVAL-001 AC-14 supersession**: EVAL-001 documented the intent of a configurable Ragas judge with an OpenAI default. EVAL-003 implements that intent with Mistral as the effective default (`RAGAS_JUDGE_PROVIDER=mistral`, pinned snapshot `mistral-large-2411`). The env var name is unchanged; only the default value changes from `openai` to `mistral`.

## Exit Codes

| Code | Meaning |
|---|---|
| `0` | All gates pass (or skipped) |
| `1` | Gate violation or error rate > 10% |
| `2` | Schema/CLI error (invalid golden set, missing `--mode`, invalid baseline) |
| `3` | Workers unreachable at startup |

## Gates

**Gate A** (absolute, live mode only): `faithfulness ≥ 0.85`, `answer_relevancy ≥ 0.85`,
`context_precision ≥ 0.70`, `context_recall ≥ 0.70`.

**Gate B** (no-regression, both modes when `--baseline` provided):
- Ragas metrics: tolerances `faithfulness -0.02`, `answer_relevancy -0.02`,
  `context_precision -0.03`, `context_recall -0.03`.
- Offline deterministic: `context_recall_structural -0.05`, `keyword_overlap_rate -0.05`.

**Note — `keyword_overlap_rate` in offline (CI) mode**: because real bge-m3 embeddings are
not available in CI (500 MB model download), the seed corpus uses hash-based pseudo-embeddings.
The retrieval order is therefore driven by cosine distance against those pseudo-embeddings,
not semantic similarity. `keyword_overlap_rate` in offline mode is a **plumbing/integration
check** — it verifies that the seed→DB→retrieve pipeline is wired correctly and that the
runner can score the retrieved chunks. It is NOT a semantic relevance signal. Semantic quality
is measured by Ragas metrics in live mode (`workflow_dispatch`). A future ticket (EVAL-002)
will introduce a real corpus seed with bge-m3 embeddings for meaningful offline retrieval
quality measurement.

## Estimated Cost (live mode)

Live eval is runnable with the Mistral judge (`mistral-large-2411`, default via `RAGAS_JUDGE_PROVIDER=mistral`).
Only `LLM_API_KEY` (Mistral) is required — no OpenAI key needed.
Pricing per Mistral API docs; costs vary, first live run required for exact estimate.

### Baseline re-bake and Gate A recalibration — deferred to EVAL-002

The `eval/baseline.json` re-bake and Gate A threshold recalibration are **deferred**.

The live CI job seeds the smoke corpus via `eval.seed_test_corpus`, which produces
pseudo-embeddings (hash-based, not real bge-m3). Freezing a baseline from such a run
would measure retrieval quality against vectors with no semantic content — worse than no
baseline. A baseline-worthy run requires a future CI seed at real corpus + real bge-m3
embeddings. That seed is the blocking precondition of **EVAL-002** (already referenced
at line 55 above). Until EVAL-002 ships, `eval/baseline.json` remains the bootstrap
all-zeros (`git_sha: "initial"`, `metrics: null`) and Gate A thresholds stay as-is.

## Baseline Management

`eval/baseline.json` is version-controlled (exception `!eval/baseline.json` in `.gitignore`).
Only a human updates the baseline via an explicit commit:

```
chore(eval): bump baseline
```

If the commit message matches that pattern AND only `eval/baseline.json` changed,
Gate B is automatically skipped for that PR.

## CI Fixture

`eval/fixtures/ci_smoke_qa.jsonl` — 8 sanitized entries (4 modes, non-spoiler contexts).
Used in PR CI offline mode. Human refreshes if golden set schema changes.
