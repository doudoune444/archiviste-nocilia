# Review — INFRA-002c

## Verdict
BLOCK

## Findings

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| `.github/workflows/deploy.yml:122` | HIGH | Spec violation AC-12 step 6 (amended) — static `PREVIOUS` sentinel | `--to-revisions=PREVIOUS=100` on gateway rollback. Spec line 38/71 explicitly states "il n'existe pas de sentinel `PREVIOUS` côté gcloud — résolution dynamique obligatoire" via `gcloud run revisions list --service=<svc> --region=europe-west9 --sort-by=~metadata.creationTimestamp --limit=2 --format='value(metadata.name)' \| tail -1`. `gcloud` will reject this with `Revision PREVIOUS not found`, leaving traffic on the broken canary if it had received any. | Replace with dynamic resolution shell step that lists the last 2 revisions sorted by creationTimestamp DESC and picks `tail -1` (the N-1 revision name) before the `update-traffic` call. |
| `.github/workflows/deploy.yml:130` | HIGH | Spec violation AC-12 step 6 (amended) — static `PREVIOUS` sentinel | Same defect on workers rollback. Same `PREVIOUS=100` literal. | Same fix. Resolve dynamically per-service before `update-traffic`. |
| `.github/workflows/deploy.yml:90-92` | HIGH | Spec violation AC-12 step 4 (amended) — smoke test does not parse JSON `.status` | `curl -sf --max-time 30 "${CANARY_URL}/healthz"` — HTTP 2xx only. Spec line 36 explicitly requires `curl -sf <canary-url>/healthz \| jq -e '.status == "ok"'` because gateway `/healthz` returns 200 even when workers are degraded (cf gateway/src/handlers/health.rs). Current impl will promote degraded builds. | Pipe response into `jq -e '.status == "ok"'`. Fails the step on any non-ok status field. |
| `docs/runbook/rollback.md:12` | HIGH | Spec violation — runbook propagates the static `PREVIOUS` myth | Line 12: ``gcloud run services update-traffic --to-revisions=PREVIOUS=100``. Same gcloud-doesn't-have-this-sentinel issue. Runbook xref to `deploy.yml` is now misleading. | Document the dynamic resolution command (`gcloud run revisions list --sort-by=~metadata.creationTimestamp --limit=2 \| tail -1`) used by the workflow. |
| `.github/workflows/deploy.yml:75-78, 88-91` | MED | Race / lookup correctness — `--filter='metadata.name~canary'` to identify the just-deployed revision | The `gcloud run deploy ... --tag canary` reassigns the `canary` tag from the previous canary to the new one, but the filter `metadata.name~canary` is a name substring regex, not a tag filter. If revision names don't actually contain "canary" (Cloud Run names are `<service>-<random>-<rev>`, the tag is metadata, not the name), this lookup returns empty and `steps.deploy_gateway.outputs.revision` is empty → subsequent `--to-revisions==100` is malformed. | Use `gcloud run services describe <svc> --region=<r> --format='value(status.latestCreatedRevisionName)'` immediately after `gcloud run deploy` to obtain the canonical revision name. Far more reliable than substring-matching on names. |
| `.github/workflows/deploy.yml:124-131` | MED | Missing `exit 1` after auto-rollback | Spec AC-12 step 6 and failure-mode line 71 explicitly require `exit 1` after the rollback so the run is marked failed. The job is already failed (`if: failure()` branch ran), so it does exit non-zero, BUT a `curl` smoke failure already set the failure status — verify there's no path where the rollback step succeeds and masks the failure. As-written, OK because of `if: failure()`, but plan line 46 + failure-mode line 71 wording ("puis `exit 1`") suggests an explicit exit at the end. | Append explicit `exit 1` to the rollback step body for clarity, matching spec wording. |
| `.github/workflows/deploy.yml:106-119` | MED | Promote steps run even if only one canary deploy failed | `if: success()` on both promote steps. If `deploy_workers` fails, gateway deploy succeeded → `success()` is false at that point, gateway promote skipped, good. BUT if both deploy steps succeed and smoke fails, both promote skipped (correct). Concern: there is no smoke test for workers. Workers has `ingress=internal` so direct *.run.app curl from a GHA runner won't work, but the spec requires verifying the canary stack end-to-end. Gateway healthz at `/healthz` does (per gateway code) check workers connectivity → defensible. Document this. | Add a comment justifying why only gateway is smoke-tested (`/healthz` exercises workers reachability via internal service call). Otherwise spec AC-12 step 4 reads as covering both. |
| `.github/workflows/deploy.yml` | MED | Out-of-scope: secret indirections not documented in plan | Plan PR c does not list `secrets.GCP_WIF_PROVIDER`, `secrets.GCP_SA_EMAIL`, `secrets.GCP_PROJECT_ID` requirements. These must be configured in the repo settings for the workflow to function. Not a code defect, but post-merge the first run will fail without operator setup. | Add a section to `docs/runbook/bootstrap-gcp.md` (or rollback runbook) listing the 3 required GitHub Actions secrets and how to source their values from Terraform outputs. |
| `.github/workflows/deploy.yml` | LOW | actionlint not run locally (binary unavailable on Windows worktree) | Cannot confirm syntax/expression validity. Per plan line 77, `actionlint` step in `ci.yml` should cover `deploy.yml` automatically on PR. | Verify CI actionlint job passes on the PR before merging. Manual confirmation required. |
| `.github/workflows/deploy.yml:1-132` | LOW | No timeout on `gcloud run deploy` steps | Build-and-deploy steps lack `timeout-minutes`. A stuck Cloud Run deploy could burn 6h of runner time. | Add `timeout-minutes: 15` at job or step level. |

## Spec coverage

- AC-11 (WIF auth, no JSON key): pass — workflow uses `google-github-actions/auth@v2` with `workload_identity_provider` + `service_account`. `! grep credentials_json` confirmed (zero matches). `! grep GCP_SA_KEY` confirmed.
- AC-12 step 1 (build images): pass — `docker/build-push-action@v6` × 2.
- AC-12 step 2 (push to AR `<region>-docker.pkg.dev/<project>/archiviste/{gateway,workers}:<git_sha>`): pass — tags use `github.sha`.
- AC-12 step 3 (`--no-traffic`): pass — both services deployed with `--no-traffic --tag canary`.
- AC-12 step 4 (smoke `curl -sf <canary-url>/healthz | jq -e '.status == "ok"'`): **FAIL** — `jq` parsing absent. HTTP 2xx is the only gate. See finding 3.
- AC-12 step 5 (promote): pass — `update-traffic --to-revisions=<rev>=100` × 2, gated on `success()`.
- AC-12 step 6 (rollback via dynamic resolution + `exit 1`): **FAIL** — uses static `PREVIOUS=100` sentinel which gcloud does not recognize. See findings 1, 2, 4. Explicit `exit 1` missing (finding 6).
- AC-13 (runbook 3 cmds + PITR): pass — `rollback.md` lists `gcloud run revisions list`, `gcloud run services update-traffic`, `curl /healthz`, plus `gcloud sql backups` section.

## Property invariants

- N/A — workflow / runbook only, no property invariants from `specs/properties.md` apply.

## Security

- No secrets in code — secrets referenced via `${{ secrets.* }}` (GCP_WIF_PROVIDER, GCP_SA_EMAIL, GCP_PROJECT_ID). OK.
- No JSON service account key — confirmed by grep. WIF only. OK.
- `permissions: id-token: write, contents: read` — minimal, correct for WIF.
- No `verify=False`, no curl `-k` insecure flag. OK.
- Smoke `curl` targets `*.run.app` (Cloud Run TLS terminator) — bypasses Cloudflare DNS race per plan. Acceptable.
- `--max-time 30` on smoke curl — bounded, OK.
- Image tags use `${{ github.sha }}` — immutable, no `:latest`. OK.
- WIF auth uses provider + SA via secrets — no token logging risk.
- No shell injection vector — all `${{ secrets.* }}` interpolations are into argv tokens that `gcloud` parses; no `eval`, no user-controlled input piped into shell.

## Out-of-scope changes

- None. Diff touches only `.github/workflows/deploy.yml` (new), `docs/runbook/rollback.md` (finalisation per plan), `CHANGELOG.md` (entry). All listed in plan PR c "Files to touch".
- Diff size: 144 LOC. Under 300 LOC budget. OK.

## Summary

Two **HIGH** correctness defects directly contradicting the recently-amended spec (AC-12 step 4 JSON parsing, AC-12 step 6 dynamic revision resolution), plus a propagated copy of the broken `PREVIOUS=100` pattern in the runbook. The rollback path is non-functional as-shipped: `gcloud run services update-traffic --to-revisions=PREVIOUS=100` will error out with `Revision [PREVIOUS] not found`, meaning a failing canary cannot be automatically rolled back — the exact failure mode AC-12 step 6 was designed to handle. The smoke test gap (no `.status == "ok"` JSON check) means a gateway-up / workers-degraded build will pass smoke and promote 100%, which is the specific scenario spec line 36 calls out.

Must fix before merge:
1. Replace both `PREVIOUS=100` literals with dynamic `gcloud run revisions list --sort-by=~metadata.creationTimestamp --limit=2 | tail -1` resolution per service.
2. Replace smoke `curl -sf` with `curl -sf ... | jq -e '.status == "ok"'`.
3. Update `docs/runbook/rollback.md:12` to reflect dynamic resolution (matching workflow truth).
4. Replace revision lookup `--filter='metadata.name~canary'` with `gcloud run services describe ... --format='value(status.latestCreatedRevisionName)'`.
5. Add explicit `exit 1` at end of each rollback step (matches spec failure-mode wording).
