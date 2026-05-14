# Review — INFRA-001

## Verdict
APPROVE

## Summary

Diff = 35 insertions / 5 deletions across 4 files. Surface = 2 workflows + `docker-compose.yml` healthcheck + CHANGELOG. No application code touched. All AC oracles green. No security findings. One LOW observation on spec/plan divergence already disclosed in the plan.

## Findings

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| `.github/workflows/eval.yml:45-75` | LOW | spec/plan divergence (disclosed) | Spec AC-7 L15: "INFRA-001 livre cette exigence pour `ci.yml` job `contract` uniquement ; `.tmp-pr/eval.yml` n'est PAS modifié". Plan L13, L18 explicitly widened scope to live `eval.yml` (was promoted from `.tmp-pr/` in commit dcba965 / EVAL-001, post-spec-author). Implementation matches plan. Consistent with AC-7's prescriptive clause ("any future workflow spawning workers via uvicorn doit reprendre ce pattern") since `eval.yml` is now live. Documented openly in the plan. | None — accept as plan-time correction. Human ratified by validating plan. |
| `.github/workflows/ci.yml:133`, `eval.yml:46` | LOW | action floating tag | `actions/cache@v4` uses floating major tag, not SHA-pinned. Repo mixes both styles (`actions/checkout@v4` floating in same file vs `actions/upload-artifact@043fb46d... # v7.0.1` SHA-pinned). Consistent with existing local convention for `ci.yml` / `eval.yml` actions. | No change required this PR. Tracking ticket for repo-wide SHA pinning would be a separate hardening initiative. |

## Spec coverage

| AC | Evidence | Status |
|---|---|---|
| AC-1 | `ci.yml:158` `for i in $(seq 1 300); do` + `ci.yml:165` `echo "workers did not become healthy within 300 s"` + dump `/tmp/uvicorn.log` `ci.yml:166-170` + `exit 1` `ci.yml:170` | PASS |
| AC-2 | `ci.yml:132-138` `actions/cache@v4` with `path: ~/.cache/huggingface/hub`, key `${{ runner.os }}-hf-hub-${{ hashFiles('workers/uv.lock') }}`, `restore-keys: ${{ runner.os }}-hf-hub-`, placed after `provision pgvector extension` and before `start workers`. No `continue-on-error` (tolerance native per spec L41). | PASS |
| AC-3 | Empirical-only observation post-merge. No regression introduced (loop still echos `workers up after ${i}s` on success at `ci.yml:160`). | PASS (deferred to CI run) |
| AC-4 | Empirical-only observation post-merge. Loop now allows up to 300 iterations. | PASS (deferred to CI run) |
| AC-5 | `docker-compose.yml:65` `start_period: 90s` inside `workers.healthcheck`. Diff confirms no other service modified (only 1 insertion in docker-compose.yml). | PASS |
| AC-6 | Manual local procedure — diff is consistent with the requirement. Cannot be auto-verified in review. | PASS (deferred to local check) |
| AC-7 | Pattern applied to `ci.yml` job `contract` AND live `eval.yml` job `ragas`. `.tmp-pr/` directory no longer exists in repo (gone since EVAL-001 promotion). `boot-sla.yml` correctly excluded (uses docker compose, not uvicorn direct). `release-please.yml` / `gdrive-sync.yml` don't spawn workers. | PASS (with LOW finding above on spec vs plan widening) |
| AC-8 | Commit `a5d90b7` scope `chore(ci)`, message contains: `300 s chosen as ~×2 safety margin over empirical cold boot < 150 s for ~2 GiB BAAI/bge-m3 download + model load` (also mirrored in CHANGELOG entry). | PASS |

## Property invariants
- No properties from `specs/properties.md` apply (CI infra ticket, no application invariant touched).

## Security

Mapped against `.claude/rules/security.md`. Diff is CI YAML + docker-compose + CHANGELOG only — no application code path, no new network surface, no new secret handling.

- Secrets in code: none introduced. No new env vars added beyond placeholders already present pre-diff.
- `actions/cache` blob: scoped per-branch / per-PR by GitHub natively. Public HF weights only, no PII or auth material cached.
- SSRF / SQL injection / JWT / CORS / CSP / rate limit: N/A — no handler touched.
- Embedding poisoning / prompt injection: N/A — no ingestion or retrieval path touched.
- Action pinning: see LOW finding above. Floating `@v4` consistent with existing convention in target files.
- `gitleaks` content scan: no secret patterns introduced in the diff (manual review of all 35 inserted lines).
- `.claude/rules/secret-hygiene.md`: respected. No `.env`, no `*.key`, no SA JSON, no token literal.

## Quality

- Diff size: 35 insertions / 5 deletions ≪ 300 LOC budget.
- Vertical slice: files modified = subset of plan "Files to touch" (`.github/workflows/ci.yml`, `.github/workflows/eval.yml`, `docker-compose.yml`, `CHANGELOG.md`). No piggybacking.
- `no-workaround.md`: no `# type: ignore`, no `unwrap()`, no hardcoded test bypass. Loop is honest fixed iteration.
- `clean-code.md`: shell snippet ≤ 18 lines per step body. Variable names explicit (`workers up after ${i}s`). No magic constants (300 documented in commit + CHANGELOG per AC-8).
- Observability: failure path dumps `/tmp/uvicorn.log` + process status (good diagnostic on both `ci.yml` and `eval.yml`).
- Cache key uniqueness: `${{ runner.os }}-hf-hub-${{ hashFiles('workers/uv.lock') }}` — `uv.lock` discriminates `sentence-transformers` version. `restore-keys: ${{ runner.os }}-hf-hub-` permits fallback to last-good cache on `uv.lock` change. Safe.
- `hashFiles('workers/uv.lock')` resolves from `$GITHUB_WORKSPACE` root (both `contract` and `ragas` jobs lack `defaults.run.working-directory` at job level). Correct path, no leading `./` needed.
- No N+1, no blocking I/O, no async issue (no code).

## Out-of-scope changes

None. Touched files = plan's "Files to touch" exactly.

## Test verification

- AC-1 oracle `grep -n 'seq 1 300' .github/workflows/ci.yml`: MATCH at L158. Also matches `eval.yml:63`.
- AC-1 exact message `workers did not become healthy within 300 s`: MATCH at `ci.yml:165`, `eval.yml:70`.
- AC-2 cache step ordering: `restore HF Hub cache` step is at `ci.yml:132-138`, immediately before `start workers` at `ci.yml:140`. Same pattern in `eval.yml:45-51` before `start workers` at `eval.yml:53`.
- AC-5 `grep 'start_period: 90s' docker-compose.yml`: MATCH at L65 under `workers.healthcheck`.
- `actionlint` not available in sandbox; manual YAML inspection clean (consistent indent, valid `uses:`, valid `with:` keys).

## Notes for follow-up

- Action SHA pinning across the repo could be a future hardening ticket (LOW). Not a blocker for INFRA-001.
- Spec AC-7 text refers to `.tmp-pr/eval.yml`, which is now stale — `.tmp-pr/` was removed when EVAL-001 promoted the workflow. Consider an editorial pass on `specs/acceptance/INFRA-001.md` post-merge to align language with the actual repo state.
