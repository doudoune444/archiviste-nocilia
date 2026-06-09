# Plan — EVAL-004 First live Mistral run + re-bake baseline + Gate A recalibration

## Goal
Execute the first paid live Mistral-judged run against PROD workers over the 46-entry golden set, freeze its real scores verbatim as `eval/baseline.json`, and recalibrate `GATE_A_THRESHOLDS` from the observed canon metrics — so Gate A/B measure real quality instead of bootstrap zeros.

## Acceptance criteria recap
- AC-1: human runs `workflow_dispatch` `--mode live --set specs/golden_qa.jsonl` vs PROD workers (OIDC), 46 entries, judge `mistral` (`mistral-large-2411`); artifact `eval/runs/<ts>.json` has `runner_mode:"live"`, `totals.entries==46`, `errors/entries ≤ 0.10` else rejected/uncommitted.
- AC-2: `eval/baseline.json` byte-identical to that `eval/runs/<ts>.json` (verbatim copy, no hand edits); `runner_mode:"live"`, root `metrics` aggregated canon-only, real `git_sha`.
- AC-3: commit updating `eval/baseline.json` has message exactly `chore(eval): bump baseline`, touches ONLY `eval/baseline.json`, and is HEAD of the PR at merge (triggers EVAL-001 AC-17 Gate-B skip).
- AC-4: the `GATE_A_THRESHOLDS` edit in `eval/gates.py` is a SEPARATE commit, ordered BEFORE the baseline-bump commit.
- AC-5: each threshold = `floor((observed_canon − 0.05) × 100) / 100`, applied to the 4 root `metrics.*` of the AC-1 run.
- AC-6: recalculated threshold NEVER below floor `0.50`; if `< 0.50` for any metric → hard stop (no thresholds edited, no baseline frozen, human investigates; gate never silently disabled).
- AC-7: any threshold `0.50 ≤ new < old` = named human concession, listed `<metric> : <old> → <new> (observé <value>)` in PR body AND `eval/README.md` Gates section.
- AC-8: `eval/README.md` Gates + Estimated Cost updated for (a) post-recalibration Gate A thresholds, (b) real Mistral cost band `~$0.50–$1.00/run` (replacing `~$0.46/run`), (c) baseline run date/`git_sha`.
- AC-9: no migration, no OpenAPI, no golden_qa, no runner-code change (`eval/*.py` except `eval/gates.py`); `eval/gates.py` limited to `GATE_A_THRESHOLDS`.
- AC-10: frozen baseline self-consistent by construction — delta(baseline, baseline) == 0 ≥ Gate B negative tolerances; logical property, NO additional paid run.

## Files to touch
- `eval/baseline.json` — replaced verbatim by the AC-1 run artifact (human-only file; bump commit = HEAD).
- `eval/gates.py` — `GATE_A_THRESHOLDS` 4 values recalibrated (dict only, no logic change). Separate commit, ordered before bump.
- `eval/README.md` — Gates thresholds, named concessions, `~$0.50–$1.00/run` cost band, baseline date/`git_sha`.
- `CHANGELOG.md` — `## [Unreleased]` EVAL entry.
- NOT touched: `migrations/*`, `specs/openapi/*`, `specs/golden_qa.jsonl`, `eval/fixtures/`, any `eval/*.py` except `gates.py`.

## Test strategy
- Integration (human/AC-1): `workflow_dispatch` live run → artifact `runner_mode=="live"`, `entries==46`, `errors/46 ≤ 0.10`.
- Contract (AC-2): `diff <(jq -S . eval/baseline.json) <(jq -S . eval/runs/<ts>.json)` → 0; baseline `git_sha != "initial"`.
- Unit table (AC-5): for each metric `committed_threshold == floor((observed − 0.05)*100)/100` vs the 4 run values.
- Review (AC-6/AC-7): floor `≥ 0.50` asserted; any lowering named in README + PR body.
- Contract (AC-3/AC-4): `git log --oneline` order (gates edit before bump-HEAD), `git diff --name-only HEAD~1 HEAD == eval/baseline.json`.
- Contract (AC-8/AC-9): `grep` README for 4 thresholds + cost band + `git_sha`; `git diff` on forbidden paths → 0.
- Logic (AC-10): demonstrate delta==0 ≥ each negative tolerance — no paid run.
- No property test (`specs/properties.md` lists no Gate-A-threshold invariant). No schemathesis (no OpenAPI). No new pytest (existing `test_gates.py` already covers gate logic; thresholds are data).

## Implementation steps (ordered)
**Phase H — human operator (live op, no LOC):**
1. Confirm Mistral pricing; ensure `LLM_API_KEY` (Mistral, with credit) present in `workflow_dispatch` env; confirm PROD workers reachable (OIDC OBS-007/OBS-009).
2. Resolve the CI-workflow judge-provider blocker (see Risks #1) BEFORE dispatch — the run MUST execute with `RAGAS_JUDGE_PROVIDER=mistral`.
3. Trigger `workflow_dispatch` live run; capture `eval/runs/<ts>.json` artifact. If `errors/46 > 0.10` or judge/key/snapshot failure → reject, investigate, re-run (AC-1 / Failure modes). Do NOT proceed.
4. Read the 4 root `metrics.*` from the artifact; compute `floor((observed−0.05)*100)/100` per metric. If any `< 0.50` → HARD STOP, escalate, abort ticket (AC-6).

**Phase A — agent code/doc (after a valid artifact exists):**
5. Edit `eval/gates.py` `GATE_A_THRESHOLDS` to the 4 computed values. → commit `chore(eval): recalibrate Gate A thresholds` (or `fix(eval):`).
6. Update `eval/README.md`: thresholds, named concessions (AC-7), cost band, baseline date/`git_sha`. Update `CHANGELOG.md`. → same commit OR a doc commit, but BOTH ordered before the bump (AC-4).
7. Copy artifact verbatim into `eval/baseline.json`. → commit `chore(eval): bump baseline`, touching ONLY `eval/baseline.json`, as HEAD (AC-3).
8. Verify `git log --oneline` order + `git diff --name-only HEAD~1 HEAD == eval/baseline.json` before pushing.

## Risks / open questions
- **Blocker (must resolve before dispatch):** `.github/workflows/eval.yml:236` hardcodes `RAGAS_JUDGE_PROVIDER: openai` in the live job. AC-1 requires the baseline run be judged by `mistral`. The spec names only the OBS-009 *prod-job* judge mismatch as out-of-scope — it does NOT cover this CI-workflow line. As written, dispatching produces an OpenAI-judged baseline, violating AC-1. Editing `eval.yml` is NOT in AC-9's forbidden list but IS outside the spec's named Touch points. Per no-workaround.md: surface to human — either (a) human overrides the env at dispatch time if the workflow allows, or (b) a one-line `eval.yml` fix is human-approved as in-scope. Do not silently proceed with `openai`.
- AC-7 concession depends on observed values: if any canon metric `< 0.85` (faithfulness/answer_relevancy) or `< 0.70` (context_precision/context_recall) but `≥ 0.50`, the lowered threshold MUST be named. Values unknown until the run completes.
- `write_run` serializes with `sort_keys=True`; the verbatim copy is already canonically ordered, so AC-2's `jq -S` diff is trivially 0. Ensure the copy preserves trailing newline / exact bytes (use file copy, not re-serialize).
- Cost band `~$0.50–$1.00` is an estimate; the real run's `started_at`/`finished_at` + Mistral invoice are the only ground truth. Documented, not capped (per spec).
- OBS-009 PROD job runs `RAGAS_JUDGE_PROVIDER=openai` (`cloud_run_job.tf`) vs this baseline's `mistral` — named follow-up, NOT resolved here.

## Out of scope
- Realigning OBS-009 prod job judge (openai→mistral) — explicit follow-up ticket.
- Any change to `GATE_B_TOLERANCES` / `GATE_B_OFFLINE_TOLERANCES`.
- Runner code, `/v1/generate` path, `LLM_PROVIDER`, the judge wiring (EVAL-003), system prompt.
- `specs/golden_qa.jsonl`, `eval/fixtures/ci_smoke_qa.jsonl`.
- Re-pinning the `mistral-large-2411` judge snapshot.
- Any variance/multi-run margin — fixed 0.05 on a single run.
- Any coded cost cap; any new CI gate (Gate A stays live-dispatch-only by design).
- A second paid run for AC-10 (logical property, no run needed).
