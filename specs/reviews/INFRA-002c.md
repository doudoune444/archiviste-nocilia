# Review — INFRA-002c (R3 — post-push)

## Verdict

APPROVE

R1 (BLOCK) and R2 (REQUEST_CHANGES) issues are all resolved in commits `936c4f4` (R2 fixes) and `e4c8b3e` (merge). Remaining items are LOW / informational — none blocks merge of PR #55. Adversarial sweep on the post-merge diff (185 net +) surfaces no HIGH and one MED-with-mitigation. Approving with deploy-time verification notes captured below for the first real run.

## Context

- PR #55 `feat(infra): INFRA-002c GHA deploy.yml + auto-rollback + jq smoke`
- Branch: `feat/INFRA-002c-gha-deploy` (3 commits ahead of `origin/main`).
- Files touched: `.github/workflows/deploy.yml` (+149), `docs/runbook/rollback.md` (+35/-1), `CHANGELOG.md` (+5 lines).
- Prior reviews: R1 BLOCK (e56f33c, prior content overwritten here), R2 REQUEST_CHANGES on PR-c (7569d86).
- Diff size 185 net + = well under 300 LOC vertical-slice cap.
- No conflict markers anywhere (`git grep -nE "^(<<<<<<<|=======|>>>>>>>)"` returns empty).
- YAML parses cleanly (`yaml.safe_load`).
- GitHub-hosted `ubuntu-latest` defaults `run:` to `bash --noprofile --norc -eo pipefail {0}` — `set -eo pipefail` is implicit, so `curl -sf … | jq -e …` correctly fails the step when either side errors. No explicit `defaults.run.shell: bash` needed.

## Focus zone 1 — Dynamic rollback correctness

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| `.github/workflows/deploy.yml:122-127` | OK | `gcloud revisions list` ordering | `--sort-by=~metadata.creationTimestamp --limit=2 \| tail -1` correctly resolves N-1 revision. `~` prefix = descending, so list = [current, previous], `tail -1` = previous. | — |
| `.github/workflows/deploy.yml:135-149` | OK | Independent workers rollback | Workers rollback step re-runs `gcloud revisions list` against `WORKERS_SERVICE` (no shared variable from gateway step). Correct. | — |
| `.github/workflows/deploy.yml:133, 149` | OK | `exit 1` on each rollback step | Both rollback steps end with `exit 1` after `update-traffic`. Workflow fails — no silent green. | — |
| `.github/workflows/deploy.yml:119-149` | LOW | First-deploy edge case | If `revisions list --limit=2` returns 1 row (very first deploy), `tail -1` returns the JUST-deployed canary; `update-traffic --to-revisions=<canary>=100` becomes a no-op and `exit 1` still fires correctly. Acceptable for V1 beta per spec ligne 38/71. | Optional belt-and-braces: `[ -z "${PREVIOUS}" ] && { echo "no previous revision"; exit 1; }` before update-traffic. |
| `.github/workflows/deploy.yml:101, 109` | MED | Promote-failure rollback gap | Conditional `if: success()` on promote steps + `if: failure() && steps.smoke.conclusion == 'failure'` on rollback steps means: if `promote gateway` itself fails (e.g. IAM/quota) AFTER smoke succeeded, neither rollback fires. Workflow exits non-zero but canary tag retains 0% traffic, no harm to live traffic, but operator must inspect manually. | Broaden rollback `if:` to fire on any post-deploy failure: `if: failure() && steps.deploy_gateway.conclusion == 'success'`. Recommended as follow-up ticket; not a blocker because the failure mode is safe (no traffic shift). |
| `.github/workflows/deploy.yml:120, 136` | LOW | Cross-service blast radius | If only gateway smoke fails (e.g. gateway image bad, workers image fine), workflow rolls back BOTH services. Workers revision is freshly built but reverts to N-1. V1 beta deploys-as-pair so acceptable, but operator should know. | Optional: add a comment line above rollback steps documenting the pair-rollback intent. Not blocking. |

## Focus zone 2 — jq smoke correctness

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| `.github/workflows/deploy.yml:97` | OK | curl + jq composition | `curl -sf --max-time 30 "${CANARY_URL}/healthz" \| jq -e '.status == "ok"'`. `-sf` exits non-zero on HTTP ≥ 400; `jq -e` exits non-zero on false/null. `pipefail` set by GHA default → step fails if either side fails. `--max-time 30` matches the 30 s spec cap on external calls. | — |
| `.github/workflows/deploy.yml:90-97` | MED | Canary URL field path | `gcloud run revisions describe <rev> --format='value(status.url)'` — for an untagged revision this returns the revision-level URL `https://<service>-<hash>-<region>.a.run.app`. For a tagged revision deployed with `--tag canary --no-traffic`, the tagged URL `https://canary---<service>-<hash>.a.run.app` lives on the SERVICE object under `status.traffic[].url`, not always on the REVISION's `status.url` (gcloud SDK version-dependent). If `${CANARY_URL}` is empty, `curl` will try `"/healthz"` and `-f` fails — workflow does roll back, but for the wrong reason (false-negative smoke). | First real run: visually confirm the `Smoke URL: <value>` log line (already in place at line 96) is non-empty. If empty in practice, swap to a service-level traffic-tag lookup. Track as deploy-time observation, not blocker. |
| `.github/workflows/deploy.yml:85-89` | OK | Smoke is gateway-only (intentional) | Comment block (lines 86-89) explicitly documents why workers smoke is not done: `ingress=internal` not reachable from GHA runner, gateway `/healthz` exercises workers via internal call. Matches spec AC-12 step 4 + AC-2 (workers ingress=internal). | — |
| `.github/workflows/deploy.yml:85` | OK | Bypass Cloudflare DNS race | Smoke hits the canary `*.run.app` URL directly, not `archiviste.nocilia.fr`. Comment matches R6 risk in plan. | — |

## Focus zone 3 — WIF auth

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| `.github/workflows/deploy.yml:26-29` | OK | Zero JSON keys | `google-github-actions/auth@v2` with `workload_identity_provider` + `service_account` only. No `credentials_json`, no `key-file`. `grep -nE "credentials_json\|GCP_SA_KEY\|sa-key\|key-file"` returns empty. Matches AC-11 + D-7 garde-fou. | — |
| `.github/workflows/deploy.yml:7-9` | OK | Job permissions | `id-token: write` (required for WIF), `contents: read`. Minimal. | — |
| `.github/workflows/deploy.yml:26` | MED | Action version pin | `google-github-actions/auth@v2` is a floating major tag, not a commit SHA. ci.yml pins `rhysd/actionlint@a443f344… # v1.7.9` to SHA but uses floating tags for first-party `actions/*` — project convention is mixed. `google-github-actions/*` and `docker/*` are third-party; same risk profile as `rhysd/*`. Defense-in-depth says pin SHA. | Follow-up `chore(ci)` ticket: pin SHAs for `google-github-actions/*` + `docker/*` across all workflows. Not a blocker — consistent with `googleapis/release-please-action@v4` floating-tag pattern already in repo. |

## Focus zone 4 — Merge correctness (commit e4c8b3e)

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| `CHANGELOG.md:10-15` | OK | Conflict resolution | All 4 expected bullets present under unified `### Security` heading inside `## [Unreleased]`: PR-c review fixes, PR-c initial, PR-b review fixes, SEC-003. Order matches commit chronology. `git diff e4c8b3e^1...e4c8b3e -- CHANGELOG.md` shows clean additions, no deletion of pre-existing content. | — |
| `CHANGELOG.md:17, 19` | LOW | Orphan bullets after `### Security` | Lines 17 (`chore(ci): INFRA-001…`) and 19 (`fix(gdrive_export)…`) sit between `### Security` (line 10) and `### Added` (line 21) with no intervening sub-header — they visually fall under `### Security` but were authored as un-categorised entries. Pre-existing on `main`; not introduced by this PR. | Out of scope for INFRA-002c. Optional follow-up: re-categorise under `### Added` / `### Fixed` or introduce a `### Changed` heading. |
| project-wide | OK | No conflict markers | `git grep -nE "^(<<<<<<<\|=======\|>>>>>>>)"` returns nothing. Clean merge. | — |
| project-wide | OK | No file content loss | Merge stat (only CHANGELOG.md touched) confirms no orphan deletes elsewhere. | — |

## Focus zone 5 — Secret hygiene

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| `.github/workflows/deploy.yml:28-29, 41, 51` | OK | Secrets via `${{ secrets.* }}` only | `GCP_WIF_PROVIDER`, `GCP_SA_EMAIL`, `GCP_PROJECT_ID` — all 3 read via `secrets.*` context. No inline values, no hardcoded project ID. | — |
| `docs/runbook/rollback.md:75-86` | OK | Required secrets documented | Table at lines 78-82 lists the 3 secrets with source + description. Operator can reproduce config from `terraform output`. Matches AC-13 spirit. | — |
| `.github/workflows/deploy.yml` (whole file) | OK | No `gcloud auth activate-service-account --key-file=…` | grep confirms absent. Only WIF auth path exists. ADR-banned JSON key fallback not present. | — |
| `.github/workflows/deploy.yml` (whole file) | OK | No secret printed | No `echo ${{ secrets.* }}`, no `env > $GITHUB_OUTPUT`. The only `echo` statements (lines 96, 128, 144) print non-secret revision names / canary URL. | — |

## Focus zone 6 — Pin discipline

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| `.github/workflows/deploy.yml:22, 26, 36, 46` | MED | Third-party actions on floating tags | `actions/checkout@v4` (first-party — OK per project convention, ci.yml lines 27, 40, 70, 111 all `@v4`), `google-github-actions/auth@v2` (third-party — inconsistent with `rhysd/actionlint@<SHA>` pattern), `docker/build-push-action@v6` × 2 (third-party — same). | Follow-up `chore(ci)` ticket: pin SHAs for `google-github-actions/*` + `docker/*` across all workflows for consistency. Not a blocker for this PR — matches existing `release-please.yml` pattern. |

## Focus zone 7 — Other adversarial sweeps

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| `.github/workflows/deploy.yml:31` | LOW | `gcloud auth configure-docker` ↔ buildx isolation | `gcloud auth configure-docker europe-west9-docker.pkg.dev --quiet` modifies `~/.docker/config.json` with a credential helper. `docker/build-push-action@v6` without an explicit `docker/setup-buildx-action` auto-creates a buildx instance using the `docker-container` driver; that driver runs in an isolated container and may not inherit the host config.json's credential helper. Empirically this works on GHA runners because buildx `--push` uses the host docker daemon's auth context, but it's fragile. | Add `- uses: docker/setup-buildx-action@v3` before the build steps OR add `docker/login-action@v3` with explicit AR registry credentials. First real run will surface push auth issues immediately. Track as deploy-time observation. |
| `.github/workflows/deploy.yml:42-43, 52-53` | OK | `cache-from: type=gha` / `cache-to: type=gha,mode=max` | GHA cache backend works with auto-buildx in `build-push-action@v6`. No issue. | — |
| `.github/workflows/deploy.yml` (whole file) | OK | gcloud command syntax | All `gcloud run` invocations use valid subcommands (`deploy`, `services describe`, `services update-traffic`, `revisions describe`, `revisions list`). Flags `--no-traffic`, `--tag`, `--to-revisions=<rev>=N`, `--quiet`, `--region`, `--service`, `--sort-by`, `--limit`, `--format` all valid for current gcloud SDK. | — |
| `.github/workflows/deploy.yml:13` | OK | Job naming | `name: build → push → canary → smoke → promote` — emoji-free, ASCII arrow. Cosmetic but reads cleanly. | — |
| `docs/runbook/rollback.md` (whole file) | OK | AC-13 grep contract | `grep -c 'gcloud run' docs/runbook/rollback.md` → 5 hits (lines 11, 15, 21, 35, 41) ≥ 3. `gcloud sql backups` present at line 60. PITR Cloud SQL section matches AC-13. | — |
| `.github/workflows/deploy.yml:88-89` | OK | Workers ingress comment | Comment explicitly explains why no direct workers smoke — matches spec AC-2 + AC-12 step 4 + plan line 46. No commented-out code. | — |
| `.github/workflows/deploy.yml:65-66, 80` | OK | Comment quality | WHY comments at lines 65-66 (`tag-based substring filter on metadata.name is unreliable`) and 80 (`same canonical resolution`) document non-obvious choice. Per `clean-code.md`, WHY comments are appropriate when non-obvious. | — |

## Spec coverage

- AC-11 (WIF auth, no JSON keys): covered — lines 25-29 use `workload_identity_provider` + `service_account`. Grep contract `! grep -E "credentials_json\|GCP_SA_KEY" .github/workflows/deploy.yml` passes.
- AC-12 step 1-2 (build + push): covered — lines 35-53, two `docker/build-push-action@v6` calls tagged with `${{ github.sha }}` pushing to `europe-west9-docker.pkg.dev/.../archiviste/{gateway,workers}`.
- AC-12 step 3 (`gcloud run deploy --no-traffic --tag canary`): covered — lines 56-83 for both services with `latestCreatedRevisionName` captured per step output.
- AC-12 step 4 (smoke `curl -sf <url>/healthz`): covered — line 97, plus `jq -e '.status == "ok"'` strictness which exceeds the spec literal "HTTP 2xx" (spec amended PR-c R2 fix per CHANGELOG line 12).
- AC-12 step 5 (promote 100% on smoke OK): covered — lines 100-114.
- AC-12 step 6 (rollback + `exit 1`): covered — lines 119-149 with dynamic revision resolution × 2 services.
- AC-13 (rollback runbook 3 cmds + PITR): covered — `docs/runbook/rollback.md` lines 35-49 (3 numbered cmds) + lines 56-67 (PITR Cloud SQL section).
- AC-14 (`https://archiviste.nocilia.fr/healthz` HTTP 200 post-merge): cannot be verified pre-merge — integration test deferred to first real run, matches spec "intégration end-to-end : premier run de `deploy.yml` sur merge `main`".

## Property invariants

- N/A — `specs/properties.md` has no infra/deploy invariants. Workflow logic is procedural, not algorithmic.

## Security audit

- A01 Broken Access Control: WIF condition CEL `assertion.repository == 'doudoune444/archiviste-nocilia' && assertion.ref == 'refs/heads/main'` enforced at Terraform side (PR-a); workflow `permissions: id-token: write` minimum. OK.
- A02 Cryptographic Failures: no app-level crypto added. OK.
- A03 Injection: `${{ secrets.* }}` interpolation in `run:` blocks is shell-injectable in principle, but values are GitHub-managed (operator-set, not user-input). Risk model: secret-setter is trusted operator. OK.
- A09 Logging: revision names and canary URLs echoed; no secrets printed. OK.
- A10 SSRF: smoke calls a known, deploy-pipeline-controlled Cloud Run URL — not user-supplied. OK.
- Forbidden patterns sweep: no `unwrap`, no `verify=False`, no hardcoded creds, no wildcard CORS — N/A for YAML. OK.
- Secret hygiene: matches `.claude/rules/secret-hygiene.md` (no inline secrets, no JSON SA key, secrets via GHA secrets context only).

## Out-of-scope changes

None. Files touched (`deploy.yml`, `rollback.md`, `CHANGELOG.md`) all in plan PR-c "Files to touch" (plan.md lines 42-48). No drift.

## Deploy-time observation list (post-merge first run)

These are not blockers but should be visually checked the first time `deploy.yml` runs against `main`:

1. `Smoke URL: <value>` log line (deploy.yml:96) — confirm non-empty. If empty, swap to `services describe` traffic-tag path.
2. Docker push step succeeds — if not, add `docker/setup-buildx-action@v3` and/or `docker/login-action@v3`.
3. First-run revision list returns ≥ 2 entries by the second deploy (acceptable for first-ever deploy to return only the canary).
4. WIF token exchange step prints `Setup Workload Identity Federation` success — confirms CEL condition matches.

## Summary

Three commits, 185 net +, scope clean, no HIGH findings, no secrets, no JSON keys, dynamic rollback correct, smoke composition correct, merge clean. R2 fixes verified in place. Approving with a small list of deploy-time visual checks and one MED (promote-failure rollback gap) recommended as a follow-up improvement ticket rather than a blocker.
