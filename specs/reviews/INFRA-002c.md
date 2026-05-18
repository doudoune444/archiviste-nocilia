# Review — INFRA-002c

## Round 2 (fix commit `936c4f4`)

### Verdict R2
APPROVE

### Round 1 findings resolution

| R1 finding | Severity | Status R2 | Evidence in fix |
|---|---|---|---|
| `deploy.yml:122` static `PREVIOUS=100` gateway | HIGH | RESOLVED | Lines 119-133 : `gcloud run revisions list --service=$GATEWAY_SERVICE --region=$REGION --sort-by=~metadata.creationTimestamp --limit=2 --format='value(metadata.name)' \| tail -1` puis `update-traffic --to-revisions="${PREVIOUS}=100"`. Résolution dynamique correcte (cf vérification ci-dessous). |
| `deploy.yml:130` static `PREVIOUS=100` workers | HIGH | RESOLVED | Lines 135-149 : même pattern dynamique appliqué au service workers. |
| `deploy.yml:90-92` smoke sans parsing JSON `.status` | HIGH | RESOLVED | Line 97 : `curl -sf --max-time 30 "${CANARY_URL}/healthz" \| jq -e '.status == "ok"'`. Le pipeline échoue si curl renvoie non-2xx (stdout vide → `jq -e` exit ≠ 0) OU si `.status != "ok"`. |
| `rollback.md:12` propagation du myth `PREVIOUS=100` | HIGH | RESOLVED | rollback.md lines 7-23 : nouvelle section « Workflow auto-rollback » documente la résolution dynamique exacte (commande copiée verbatim de la spec AC-12 step 6). Note explicite « il n'existe pas de sentinel `PREVIOUS` côté gcloud ». |
| `deploy.yml:75-78, 88-91` lookup révision via `--filter='metadata.name~canary'` | MED | RESOLVED | Lines 67-69 et 81-83 : remplacé par `gcloud run services describe <svc> --format='value(status.latestCreatedRevisionName)'`. Commentaire AC-12 step 3 explique pourquoi. |
| `deploy.yml:124-131` `exit 1` explicite manquant | MED | RESOLVED | Lines 133 et 149 : `exit 1` ajouté en fin de chaque step rollback. Aligné avec spec AC-12 step 6 + failure-mode line 71. |
| `deploy.yml` smoke workers absent — justification manquante | MED | RESOLVED | Lines 85-89 : commentaire documente `Workers ingress=internal: not directly curl-able from GHA runner — gateway /healthz exercises workers reachability via internal service call`. |
| `deploy.yml` 3 secrets GHA non documentés | MED | RESOLVED | rollback.md lines 73-86 : table `GCP_WIF_PROVIDER`, `GCP_SA_EMAIL`, `GCP_PROJECT_ID` avec source `terraform output` documentée. |
| actionlint non vérifié localement | LOW | DEFERRED | Toujours non lancé localement (binaire indisponible sur Windows worktree). Couverture CI via `ci.yml` actionlint step (plan line 77) — vérification post-PR. |
| `timeout-minutes` absent sur les steps `gcloud run deploy` | LOW | NOT ADDRESSED | Non corrigé. Non-bloquant (job-level fallback GHA = 360 min). Acceptable V1. |

### Round 2 gaming / correctness checks

| Check | Status | Evidence |
|---|---|---|
| Résolution N-1 réelle (pas `--limit=1`) | PASS | `--sort-by=~metadata.creationTimestamp --limit=2 \| tail -1` retourne bien N-1. `~` = tri descendant, top = canary fraîchement déployée (la plus récente), `tail -1` du résultat à 2 lignes = avant-dernière = révision promue précédente. Correct. |
| `jq -e '.status == "ok"'` exit code propagé | PASS | Dernière commande du pipeline = jq, son exit code remonte au step. `set -e` (default GHA `bash -e`) abandonne sur exit ≠ 0. Pas besoin de `pipefail` explicite ici. |
| `latestCreatedRevisionName` est la canary | PASS | `gcloud run deploy --no-traffic --tag canary` crée une nouvelle révision → devient `status.latestCreatedRevisionName` immédiatement. Pas de race sur push-to-main monothread. |
| Rollback target ≠ canary fautive | PASS | À l'instant T du rollback, la canary EST la révision la plus récente (smoke échoue après deploy mais avant promote). `tail -1` de la liste à 2 lignes pointe donc bien sur l'avant-dernière = révision actuellement à 100 %. Correct. |
| Hardcoded test values / sentinels | PASS | Aucun. `PREVIOUS` est une variable shell capturée dynamiquement, pas un sentinel statique. |
| Out-of-scope changes | PASS | Diff R2 ne touche que `.github/workflows/deploy.yml`, `docs/runbook/rollback.md`, `CHANGELOG.md` — listés dans plan PR c. |
| Diff total ≤ 300 LOC | PASS | 186 LOC additions (149 deploy.yml + 34 rollback.md + 3 CHANGELOG). |
| Security R1 acquis | PASS | Aucune régression — toujours zéro `credentials_json`, zéro `GCP_SA_KEY`, WIF only, `--max-time 30`, tags immutables `${{ github.sha }}`. |

### Spec coverage R2

- AC-12 step 4 (smoke `curl -sf <canary-url>/healthz \| jq -e '.status == "ok"'`) : **PASS** — pipeline JSON exact comme spec line 36.
- AC-12 step 6 (résolution dynamique + `exit 1`) : **PASS** — commande exacte spec line 38, plus `exit 1` final.
- AC-13 (runbook 3 cmds + PITR + workflow auto-rollback documenté) : **PASS** — section ajoutée cohérente avec workflow.

### Verdict final
APPROVE — toutes les findings R1 HIGH+MED résolues, aucune nouvelle régression, deux LOW restantes acceptables (actionlint CI-side, timeout-minutes non-bloquant V1).

---

## Round 1

### Verdict
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
