# Review — EVAL-004

## Verdict
APPROVE

## Summary
XS integrity + honest-deferral ticket. 4 deliverables, all on-target. Deferred work (baseline re-bake, Gate A recalibration, OBS-009 prod eval) is spec-sanctioned (Non-goals + Forward-pointer) and documented accurately — no dishonest deferral, no smuggled scope. All 4 ACs met. No HIGH findings.

## Findings

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| CHANGELOG.md:32 | LOW | formatting nit | EVAL-004 entry separated from the two `fix(...)` siblings by a blank line (line 33), while those siblings have no blank line between themselves. Cosmetic. | Remove the blank line at CHANGELOG.md:33 so the `### Fixed` block is contiguous. |

No HIGH or MEDIUM findings.

## AC coverage

- AC-1 (live job: `RAGAS_JUDGE_PROVIDER: mistral` explicit, no `openai`, no `OPENAI_API_KEY`): PASS.
  - `grep RAGAS_JUDGE_PROVIDER .github/workflows/eval.yml` → single hit, line 235 `mistral`. Zero `openai`.
  - `grep -rn OPENAI_API_KEY .github/workflows/` → **zero hits** across the whole workflows dir. Removed line was the only occurrence.
  - eval.yml:233 retains `LLM_API_KEY: ${{ secrets.LLM_API_KEY }}` in the corrected step.
- AC-2 (operable with Mistral key alone): PASS.
  - `eval/metrics.py:110` `provider = os.environ.get("RAGAS_JUDGE_PROVIDER", "mistral")` → default + env both resolve `mistral`.
  - `eval/metrics.py:111` api key read from `LLM_API_KEY` via `SecretStr`, NOT `OPENAI_API_KEY`. `_build_mistral_judge` (metrics.py:124-146) resolves `mistral-large-2411` (`DEFAULT_MISTRAL_JUDGE_MODEL`, line 17). No OpenAI secret on the mistral path. Confirmed by joint read of corrected YAML + metrics.py.
  - Note: even `_build_openai_judge` (metrics.py:149-170) consumes the passed `LLM_API_KEY`, never `OPENAI_API_KEY` env directly — so the removed env var was genuinely dead. The "dead reference" framing in spec/ADR/CHANGELOG is factually accurate.
- AC-3 (README: live-runnable-Mistral + deferral + follow-up ticket): PASS.
  - eval/README.md:60-62 "Live eval is runnable with the Mistral judge ... Only `LLM_API_KEY` (Mistral) is required — no OpenAI key needed."
  - eval/README.md:65 heading "Baseline re-bake and Gate A recalibration — deferred to EVAL-002".
  - Contains required substrings coherently (not keyword-stuffed): "pseudo-embeddings" (line ~71), "baseline" (multiple), "Gate A" (line 65/74), "EVAL-002" (lines 65/73/74). Cross-reference "already referenced at line 55 above" is EXACT — `grep -n EVAL-002 eval/README.md` confirms first mention at line 55.
- AC-4 (scope fence): PASS.
  - `git diff --name-only main...HEAD` = {.github/workflows/eval.yml, CHANGELOG.md, docs/adr/0011-eval-004-defer-baseline-rebake.md, eval/README.md} + the two spec/plan docs. Deliverable set is a STRICT subset of the AC-4 allowlist.
  - `git diff --stat main...HEAD -- migrations/ specs/openapi/ specs/golden_qa.jsonl eval/gates.py eval/baseline.json eval/metrics.py eval/fixtures/` → **empty** (zero forbidden changes). Only `eval/README.md` touched under `eval/`.

## ADR factual audit (docs/adr/0011-eval-004-defer-baseline-rebake.md)

- House format matches `docs/adr/0000-template.md` / `0008` siblings (Status/Date/Decider/Context/Decision/Consequences/Alternatives/References). PASS.
- "baseline remains bootstrap all-zeros (`git_sha: "initial"`, `metrics: null`)": VERIFIED against `eval/baseline.json` (git_sha `initial` line 5; all four `metrics` null lines 31-36; totals 0).
- "OBS-009 ... never implemented": VERIFIED. `grep -rln OBS-009` shows only a comment in `deploy.yml:75` and a backlog-table row in `docs/vision.md:98` — no Cloud Run Job, no Terraform resource exists. Claim accurate.
- Referenced files all exist: `docs/adr/0008-eval-001-loc-waiver.md` present; EVAL-003 sha `213e11c` matches git log; README section title quoted verbatim.
- `mistral-large-2411` pin matches `eval/metrics.py:17`. PASS.

## Property invariants
- `specs/properties.md` lists no invariant relevant to a CI-config/doc change. No property test required. N/A.

## Security
- A09: secret surface REDUCED — `OPENAI_API_KEY` reference removed; zero dangling `secrets.OPENAI_API_KEY` remaining (`grep -rn` clean). `LLM_API_KEY` flows through `pydantic.SecretStr` (metrics.py:111). No new secret, no leakage.
- No SQL/SSRF/path-traversal/CORS/CSP surface touched (config + docs only).
- No hardcoded credentials introduced. `LLM_API_KEY: ci-placeholder` (eval.yml:108) is pre-existing, in the unrelated offline "start workers" step, untouched.

## Lint / parse verification (exactly what ran)
- YAML parse: `python -c "yaml.safe_load(open('.github/workflows/eval.yml'))"` → **YAML OK** (passed).
- `actionlint`: **NOT installed locally** — could not run. AC-1 oracle calls for actionlint; CI (`ci.yml`) + pre-commit will run it. Indentation of the edited env block inspected by hand (eval.yml:231-242): consistent 2-space nesting, valid. Not claiming actionlint-green.
- `markdownlint`: **NOT installed locally** — could not run on README/ADR/CHANGELOG. Manual inspection only.
- No cargo/ruff/mypy/pytest run: ticket changes zero Rust/Python source; not applicable.

## Out-of-scope changes
- None. `specs/acceptance/EVAL-004.md` + `specs/plans/EVAL-004.md` appear in the diff but are the ticket's own spec/plan (re-draft history), expected, not code scope.

## LOC
- Deliverable diff well under 300 (≈ +75 lines incl. 63-line ADR). Within vertical-slice budget.
