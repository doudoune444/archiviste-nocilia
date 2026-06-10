# ADR 0011 — Defer baseline re-bake + Gate A recalibration; park OBS-009

- Status: accepted
- Date: 2026-06-10
- Decider: humain (auteur du projet)

## Context

EVAL-003 wired the Mistral judge as the default for live Ragas evaluation
(`eval/metrics.py`, default `mistral`, pinned `mistral-large-2411`), but the
live CI job in `.github/workflows/eval.yml` still forced `RAGAS_JUDGE_PROVIDER:
openai` and referenced a non-existent `OPENAI_API_KEY` secret, making
`--mode live` in CI inoperable.

Additionally, the live job seeds the smoke corpus via `eval.seed_test_corpus`
using pseudo-embeddings (hash-based, not real bge-m3). Freezing `eval/baseline.json`
from such a run would capture retrieval quality against vectors with no semantic
meaning — the baseline would be meaningless and potentially harmful as a regression
gate. A baseline-worthy run requires a real-corpus + real bge-m3 CI seed, which is
the blocking precondition for EVAL-002.

OBS-009 (Cloud Run Job for production live eval) was scoped independently but
never implemented; the effort/value ratio is unfavourable until the baseline
infrastructure is stable. No path forward is planned.

## Decision

1. **Fix live CI judge to Mistral only**: remove `OPENAI_API_KEY` from the `run
   eval live` step env block; set `RAGAS_JUDGE_PROVIDER: mistral` explicitly.
   Only `LLM_API_KEY` (Mistral) is required for live runs.

2. **Defer baseline re-bake and Gate A recalibration to EVAL-002**: `eval/baseline.json`
   remains the bootstrap all-zeros (`git_sha: "initial"`, `metrics: null`) and
   `GATE_A_THRESHOLDS` in `eval/gates.py` are untouched. These will be set by
   EVAL-002 once a real-corpus + bge-m3 CI seed produces a baseline-worthy run.

3. **Park OBS-009**: the prod live-eval Cloud Run Job is abandoned. No Terraform
   resource, no realignment of the prod judge, no infra PR. Decision is
   reversible if the need is re-opened in a future ticket.

## Consequences

- Live CI eval (`workflow_dispatch`) is now operable with the Mistral key alone.
- Secret surface reduced: `OPENAI_API_KEY` no longer referenced in any CI step.
- Gate A thresholds remain permissive bootstrap values until EVAL-002 ships.
- Any future live run before EVAL-002 will exercise the full pipeline but Gate A
  results should be interpreted as smoke (not quality gates).

## Alternatives considered

- **Re-bake baseline now from pseudo-embedding run**: rejected — the baseline
  would encode meaningless retrieval scores, actively misleading the Gate A
  regression check.
- **Ship OBS-009 alongside this fix**: rejected — no demand driver, adds Terraform
  complexity without a stable baseline to compare against.

## References

- `specs/acceptance/EVAL-004.md`
- `eval/README.md` §"Baseline re-bake and Gate A recalibration — deferred to EVAL-002"
- `docs/adr/0008-eval-001-loc-waiver.md` (EVAL-001 runner context)
- EVAL-002 (real-corpus + bge-m3 seed — blocking precondition)
- EVAL-003 (Mistral judge wiring — `213e11c`)
