# Review — SEC-006 PR-B (infra + docs)

## Verdict

**APPROVE** (with one MED nit-pick the implementer may defer).

## Diff scope

| File | +/- | Status |
|---|---|---|
| `infra/terraform/cloud_run.tf` | +26/-1 | EDIT (ingress flip + 3 co-fixes) |
| `infra/terraform/checks.tf` | +19 | NEW |
| `infra/terraform/tests/workers_iam.tftest.hcl` | +37 | NEW |
| `docs/runbook.md` | +44 | EDIT |
| `CHANGELOG.md` | +2 | EDIT |
| **Total** | **+128 / -1** | well under 300 LOC cap |

Note: `git diff main..HEAD` also shows `specs/plans/SEC-006.md` deletion (-208). False positive — PR-B branch is one commit behind main (missing `acebaee docs(plan): SEC-006`). The plan file exists on `main` and is preserved by the eventual merge / fast-forward. Implementer should rebase before opening the PR to avoid review-time noise; otherwise the diff inspector will flag a phantom delete.

## Critical issues (must-fix, block merge)

None.

## Major issues (should-fix this PR)

None.

## Minor issues (nice-to-have)

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| branch | LOW | branch base out of date | `git log HEAD..main` shows 1 commit (`acebaee docs(plan)`); the diff vs main therefore lists `specs/plans/SEC-006.md` as deleted (-208) which is a false positive but will confuse the PR reviewer / mergify | rebase `feat/SEC-006-pr-b-workers-ingress-iam-check` onto current `main` before opening the PR |
| `infra/terraform/tests/workers_iam.tftest.hcl:27-37` | LOW | second-run assertion strength | `accepts_runtime_sa` overrides `member = "serviceAccount:archiviste-runtime@my-project.iam.gserviceaccount.com"` but has no positive assertion. Terraform `test` will report this run as passing on any plan that doesn't raise a check failure — including unrelated regressions. Acceptable but could be tightened with an explicit `assert { condition = google_cloud_run_v2_service_iam_member.workers_runtime_invoker.member == "serviceAccount:..." }` in the same run block | optional — current form satisfies AC-10 literally (the two cases the spec demands) |
| `docs/runbook.md:281-282` | LOW | runbook completeness | `404` / `502` listed as "not a security breach" but `403` other than the expected one isn't explicitly listed; reader may assume any 403 is fine. Spec AC-11 says "MUST retourner exactement `403`" — fine here since 403 is the expected | optional: clarify "403 with any body other than the standard Cloud Run IAM denial may indicate a custom rejection path" |

## AC compliance table

| AC | Status | Evidence |
|---|---|---|
| AC-9 (ingress flip + workers port 8000 + LLM_PROVIDER + LLM_MODEL) | **PASS** | `cloud_run.tf:128` flipped to `INGRESS_TRAFFIC_ALL`; `cloud_run.tf:160-162` `ports { container_port = 8000 }`; `cloud_run.tf:178-186` two `env` blocks for `LLM_PROVIDER=mistral` + `LLM_MODEL=mistral-small-latest`. Workers `workers_runtime_invoker` IAM binding at `cloud_run.tf:234-240` UNCHANGED (SA-only, no public binding). `INGRESS_TRAFFIC_INTERNAL_ONLY` no longer appears anywhere (`grep` returns 0). Port 8000 matches `scripts/check-ports.sh` canonical (workers=8000). |
| AC-10 (Terraform check block) | **PASS** | `checks.tf:11-19` single `check "workers_iam_no_public_invoker"` block; one `assert`; literal reference to `google_cloud_run_v2_service_iam_member.workers_runtime_invoker.member` (no data source, per OQ-1); rejects both `"allUsers"` AND `"allAuthenticatedUsers"`; WHY-comment block at lines 5-10 explicitly calls out drift risk (future 2nd IAM member resource → assert must be extended). HCL syntactically well-formed on static read. `error_message` mentions ticket ID (`SEC-006 AC-10`). Test fixture at `tests/workers_iam.tftest.hcl` provides two `run` blocks: `rejects_all_users` (`expect_failures = [check.workers_iam_no_public_invoker]`) and `accepts_runtime_sa`. Both use `command = plan` (no `apply` — does not touch real infra). `override_resource` is the correct Terraform ≥1.7 mechanism for the documented use case; `versions.tf` pins `required_version = ">= 1.6"` so `expect_failures` is supported. **Minor caveat**: `override_resource` is technically a Terraform 1.7+ feature — confirm by running `terraform test` locally; if 1.6 is the only enforced floor and CI uses 1.6.x, the fixture will fail to parse. Recommend bumping `required_version` to `>= 1.7` if the test must run on the floor. |
| AC-11 (runbook smoke check) | **PASS** | `docs/runbook.md:257-299` new section "Post-deploy smoke check — workers IAM ingress (SEC-006)". All three elements present: (a) `curl -sw '%{http_code}\n' -o /dev/null https://<workers-url>/health` → expected `403` (line 271-274); the `200` critical-IAM-regression call-out is spelled out verbatim (line 278-280); (b) gateway end-to-end `POST /v1/chat` → expected `200` (line 287-290); (c) cross-reference to `infra/terraform/checks.tf` `check "workers_iam_no_public_invoker"` AC-10 (line 298) and AC-11 (line 299). |
| AC-12 (CHANGELOG entry) | **PASS** | `CHANGELOG.md:30` under `## [Unreleased]` `### Security` heading (line 28). Wording is verbatim from spec AC-12 (single line). |

## Security audit

- **IAM trust boundary preserved**: workers `workers_runtime_invoker` binding (`cloud_run.tf:234-240`) UNCHANGED — `role = "roles/run.invoker"`, `member = "serviceAccount:${google_service_account.archiviste_runtime.email}"`. No `allUsers` / `allAuthenticatedUsers` member introduced anywhere on the workers service. The only `allUsers` binding in `cloud_run.tf` (line 229) targets the `gateway` service, intentional per pre-existing HIGH-3 comment — not a regression.
- **Defense in depth**: pre-deploy gate via Terraform `check {}` (`checks.tf`) + post-deploy gate via runbook curl-403 smoke. Both reference the same invariant from independent angles.
- **No secret committed**: `LLM_PROVIDER` / `LLM_MODEL` are non-secret model identifiers. `MISTRAL_API_KEY` injection at `cloud_run.tf:203-212` uses `secret_key_ref` to Secret Manager (pre-existing, untouched by this PR). No inline credentials, no `*.tfvars`, no service account JSON.
- **No new attack surface from ingress flip**: `INGRESS_TRAFFIC_ALL` exposes the public Cloud Run endpoint, but IAM `roles/run.invoker` SA-only binding makes the endpoint return 403 to any caller without a valid ID token signed for the workers audience. The pattern is the standard Google Cloud Run "Authenticating service-to-service" guidance.
- **No SSRF / injection / etc. surface added** — diff is pure infra + docs + Markdown.
- **gitleaks-class patterns**: none detected in the diff.

## Out-of-scope drift check

- No changes to `gateway/**` (correctly deferred to PR-A, already merged per `CHANGELOG.md:30`).
- No changes to `.github/workflows/**` (CI wiring for `terraform test` correctly deferred per plan §Out of scope).
- No changes to `workers/**`.
- All 5 touched files are in plan PR-B §"Files to touch". No piggyback refactor.

## Gaming patterns

- `checks.tf` references the **literal** production resource address — not a mock, not a data source that could silently return empty. The check runs in the real `terraform plan`/`apply` cycle.
- `tests/workers_iam.tftest.hcl` `expect_failures = [check.workers_iam_no_public_invoker]` references the real check block by its canonical address. `override_resource` mutates the real resource graph rather than a shadow module — the test actually exercises the production check.
- No hardcoded test values, no swallowed errors, no disabled tests, no stub returns. Implementation is the actual production guard.

## Summary

PR-B cleanly delivers AC-9 through AC-12 in +128/-1 LOC, well below the 300-LOC vertical-slice cap. The Terraform `check {}` block (`checks.tf`) and its `.tftest.hcl` fixture form a non-trivial security gate that references the real production IAM resource — not a mock. IAM trust boundary (`workers_runtime_invoker` SA-only) is preserved exactly. Two LOW-severity recommendations: rebase the branch to eliminate the phantom `specs/plans/SEC-006.md` delete in the PR diff, and confirm `override_resource` is supported by the Terraform version CI will run (bump `required_version` to `>= 1.7` if needed). Approve.
