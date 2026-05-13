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
| `RAGAS_JUDGE_PROVIDER` | Live only (default `openai`) | LLM judge provider for Ragas metrics |
| `LLM_API_KEY` | Live only | API key for the LLM judge (Ragas) |

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

## Estimated Cost (live mode)

~$0.01/sample with OpenAI gpt-4o as Ragas judge (46 entries ≈ $0.46/run).

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
