# Review — INFRA-002b (R3 — post-merge)

## Verdict
APPROVE

## Context

R3 post-merge review of PR #54 `feat(infra): INFRA-002b Terraform Cloudflare`.
Branch contains 3 commits vs `origin/main`:
- `70f6ce8` — initial PR-b (cloudflare.tf + stubs in main.tf/variables.tf/versions.tf)
- `bf254bc` — R2 review fixes (ssl=`strict`, bot_fight_mode=`on`, .net via `cloudflare_ruleset`, scope creep removed)
- `2e42aa9` — `chore(infra): merge origin/main` (PR-a now in `main` — pulled real terraform core in)

No prior `specs/reviews/INFRA-002b.md` file in repo history; R2 findings reconstructed from `bf254bc` commit message + CHANGELOG entry (lines 12 CHANGELOG.md) which lists HIGH/MED already addressed.

Diff scope vs `origin/main` (post-merge):

| File | + | – |
|---|---|---|
| infra/terraform/cloudflare.tf | 153 | 0 |
| infra/terraform/variables.tf | 11 | 0 |
| infra/terraform/main.tf | 4 | 0 |
| infra/terraform/versions.tf | 4 | 0 |
| docs/runbook/bootstrap-gcp.md | 19 | 6 (renumber + section §7 Cloudflare token) |
| CHANGELOG.md | 1 | 0 |

Total ≈ 192 net additions, well within ≤ 300 LOC vertical slice cap.

## Merge resolution audit (focus zone 1)

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| infra/terraform/main.tf | OK | no leftover stub | merge cleanly replaced stub comment "PR-b adds only the Cloudflare provider" with real providers `google`/`google-beta`/`locals.labels` from PR-a, then appended `provider "cloudflare"` block (lines 19-21). No conflict markers, no double declaration. | — |
| infra/terraform/variables.tf | OK | no leftover stub | stub comment dropped; PR-a vars (`project_id`, `region`, `github_repo`, `domain`, `billing_account`, `budget_email`) at lines 1-32 + PR-b vars (`cloudflare_account_id`, `cloudflare_api_token`) at lines 34-43. No duplication. | — |
| infra/terraform/versions.tf | OK | no leftover stub | stub comments dropped; single `terraform{}` block with all 3 providers (google/google-beta/cloudflare) + backend GCS. No duplicate block. | — |
| infra/terraform/cloudflare.tf | OK | references resolve | `google_cloud_run_v2_service.gateway` (line 47) now resolves to `cloud_run.tf:8` from PR-a in main. `google_cloud_run_domain_mapping.archiviste_fr` cross-PR ref intact. | — |
| docs/runbook/bootstrap-gcp.md | OK | clean renumber | §7 "Cloudflare API token" inserted; previous §7 IAM DB verify → §8; §8 verify → §9; §9 post-apply → §10. Continuous numbering 1-10, no gaps/dupes. | — |
| CHANGELOG.md | OK | no double entry | single `### Security` section under `## [Unreleased]` with INFRA-002 PR-b bullet (line 12) + SEC-003 bullet (line 13). Pre-merge orphan unsectioned bullet replaced with proper `### Security` header. | — |
| repo-wide | OK | no conflict markers | `git grep -lE "^(<<<<<<<\|=======\|>>>>>>>)"` returns nothing. | — |

## Cloudflare provider v4 correctness (focus zone 2)

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| cloudflare.tf:58 | OK | `ssl = "strict"` | provider v4 enum for "Full (strict)" — correct. R2 fix held. | — |
| cloudflare.tf:63 | OK | `bot_fight_mode = "on"` | provider v4 `cloudflare_zone_settings_override.settings.bot_fight_mode` accepts string `"on"`/`"off"`. R2 fix held. | — |
| cloudflare.tf:33 | OK | `cloudflare_record.value` | v4 attribute name `value` (v5 renamed to `content`). Correct for `~> 4` pin in versions.tf. | — |
| cloudflare.tf:131-153 | OK | `cloudflare_ruleset` for .net | `kind = "zone"` + `phase = "http_request_dynamic_redirect"` + `action = "redirect"` + nested `action_parameters.from_value.target_url.expression` — matches v4 schema. `preserve_query_string = true` correct. | — |
| cloudflare.tf:144 | LOW | redirect expression drops query | `target_url.expression = concat("https://archiviste.nocilia.fr", http.request.uri.path)` — preserves path only. Query string is preserved separately via `preserve_query_string = true` (line 146), so behaviour matches `.com`/`.org`/`.eu` Page Rules. OK semantically; could note that `http.request.uri` (full) would be equivalent. | acceptable as-is |
| cloudflare.tf:68-85 | LOW | `cloudflare_rate_limit` deprecated | resource still functional in provider v4 but marked deprecated in favor of `cloudflare_ruleset http_ratelimit`. Forward-compat concern for v5 migration. Out-of-scope for this PR. | follow-up ticket post-v5 bump |

## Page Rules free-plan quota (focus zone 3)

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| cloudflare.tf:91-128 | OK | 3 Page Rules + 1 ruleset | `.com` zone: 1 Page Rule. `.org` zone: 1 Page Rule. `.eu` zone: 1 Page Rule. `.net` zone: 0 Page Rule, 1 `cloudflare_ruleset`. Each zone is independent (Page Rules quota is per-zone, not per-account), so even if all 4 used Page Rules it would still be 1/3 per zone. The migration is conservative + forward-compatible. R2 fix held. | — |

## Secret hygiene (focus zone 4)

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| variables.tf:42 | OK | `sensitive = true` | `cloudflare_api_token` declared `sensitive = true`. Confirmed. | — |
| main.tf:20 | OK | token wired by var ref | `provider "cloudflare" { api_token = var.cloudflare_api_token }` — no hardcoded value, no fallback default. | — |
| bootstrap-gcp.md:124-133 | OK | token storage doc | §7 documents token lives in `terraform.tfvars` (gitignored, per spec pre-condition) + GHA secret `CLOUDFLARE_API_TOKEN`. Not stored in GCP Secret Manager (cohérent spec L62-63: "il n'entre PAS dans Secret Manager GCP"). | — |
| bootstrap-gcp.md:62-63 | OK | tfvars example sanitized | `cloudflare_account_id = "<CF_ACCOUNT_ID>"` / `cloudflare_api_token = "<CF_API_TOKEN>"` — placeholders only, no real secret leaked. | — |
| .gitignore (verified out-of-band) | OK | tfvars gitignored | already covered by global `*.tfvars` rule per `.claude/rules/secret-hygiene.md` line 8. | — |

No high-entropy strings, no JWT/PAT/OAuth tokens in diff. `gitleaks` would pass.

## Rate-limit rule presence (focus zone 5)

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| cloudflare.tf:68-85 | OK | AC-8 rate-limit | `threshold = 100`, `period = 60` (= 100 req/min), `url_pattern = "archiviste.nocilia.fr/*"`, `schemes = ["HTTPS"]`, `action.mode = "challenge"` (allowed value per AC-8 "block or challenge"), `timeout = 300`. Maps exactly to AC-8 line 27. | — |

## DNS → Cloud Run alignment (focus zone 6)

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| cloudflare.tf:29-35 | OK | CNAME target | `archiviste.nocilia.fr` CNAME → `ghs.googlehosted.com` (Google's documented Cloud Run custom domain endpoint). `proxied = true` ensures Cloudflare TLS termination + WAF coverage. | — |
| cloudflare.tf:38-49 | OK | domain mapping cross-PR ref | `google_cloud_run_domain_mapping.archiviste_fr` `spec.route_name = google_cloud_run_v2_service.gateway.name` correctly references gateway service (cloud_run.tf:8 in main, name `archiviste-gateway`). `metadata.namespace = var.project_id` correct convention. `name = var.domain` resolves to `archiviste.nocilia.fr` default. | — |

## Spec coverage (AC-8)

| Sub-criterion | Status | Evidence |
|---|---|---|
| Cloudflare zone for `nocilia.fr` (data source allowed) | ✓ | cloudflare.tf:3-6 |
| DNS `archiviste.nocilia.fr` → Cloud Run (CNAME `ghs.googlehosted.com`) | ✓ | cloudflare.tf:29-35 |
| Proxy Cloudflare ON | ✓ | cloudflare.tf:34 `proxied = true` |
| TLS Full Strict | ✓ | cloudflare.tf:58 `ssl = "strict"` (provider v4 enum) |
| Bot Fight Mode ON | ✓ | cloudflare.tf:63 `bot_fight_mode = "on"` |
| Security Level medium | ✓ | cloudflare.tf:59 `security_level = "medium"` |
| Challenge Passage 1800 s | ✓ | cloudflare.tf:60 `challenge_ttl = 1800` |
| Rate-limit 100 req/min/IP on hostname, action block or challenge | ✓ | cloudflare.tf:68-85 |
| 4 × 301 redirects `.com`/`.org`/`.eu`/`.net` → `.fr/$1` | ✓ | cloudflare.tf:91-153 (3 page_rule + 1 ruleset, all 301, all proper target) |

All 9 sub-criteria of AC-8 satisfied.

## Property invariants

INFRA-002 has no entry in `specs/properties.md` (infra/HCL, not application code). Not applicable.

## Out-of-scope changes

None. Plan §B "Files to touch" lists exactly: `versions.tf`, `variables.tf`, `cloudflare.tf`, `bootstrap-gcp.md`, `CHANGELOG.md`. Implementation touches:
- `infra/terraform/cloudflare.tf` (in scope)
- `infra/terraform/versions.tf` (in scope)
- `infra/terraform/variables.tf` (in scope)
- `infra/terraform/main.tf` (provider block — strictly required, justified by R2 scope-creep fix that already corrected and CHANGELOG documents)
- `docs/runbook/bootstrap-gcp.md` (in scope, §7)
- `CHANGELOG.md` (in scope)

Merge commit (`2e42aa9`) restores PR-a's actual content in `main.tf`/`variables.tf`/`versions.tf` — not out-of-scope; this is the natural consequence of PR-a being merged before PR-b.

## Security checklist

- Secrets: token `sensitive=true`, no hardcoded values, no leaked tfvars in repo. ✓
- TLS: Cloudflare→origin TLS Full Strict (cert-validated). ✓
- Rate limit: 100 req/min/IP on the public hostname (exceeds default 60/min from `.claude/rules/security.md` A04 — stricter, OK). ✓
- DNS/SSRF: not applicable (no user-supplied URL fetch in HCL). ✓
- Trust boundaries: Cloudflare proxy ON, domain mapping correctly points to gateway service only (not workers, which stays `INGRESS_TRAFFIC_INTERNAL_ONLY` per PR-a `cloud_run.tf:83`). ✓
- CORS / CSP / HSTS: not in this PR scope (SEC-003 handles HSTS, already merged). ✓
- Forbidden patterns (`.claude/rules/security.md` lines 122-130): none present in diff. ✓

## LOC budget

192 net additions vs ≤ 300 LOC cap. PASS.

## Summary

The merge commit (`2e42aa9`) was executed cleanly:
- No leftover conflict markers anywhere
- No double-declared resources, variables, providers, or locals
- All cross-PR references (`google_cloud_run_v2_service.gateway`) resolve correctly post-merge
- `bootstrap-gcp.md` section renumbering coherent (§1-§10 continuous)
- CHANGELOG entry sits under proper `### Security` header without duplication

All R2 HIGH/MED findings (per `bf254bc` commit message + CHANGELOG line 12) are held:
- HIGH: `ssl = "strict"` (was `"full_strict"`)
- HIGH: `bot_fight_mode = "on"` added
- MED: scope creep removed (no duplicate providers/vars from PR-a)
- MED: `.net` redirect via `cloudflare_ruleset` (Page Rule quota preserved)

Two LOW notes for follow-up (not blockers, not in this PR scope):
1. `cloudflare_rate_limit` is deprecated in v4 — migrate to `cloudflare_ruleset http_ratelimit` when bumping to provider v5.
2. Forward-compat for provider v5: `cloudflare_record.value` will become `content`, `cloudflare_page_rule` is fully replaced by `cloudflare_ruleset`.

HIGH:0, MED:0, LOW:2 → APPROVE per agent rubric (HIGH:0 AND MED ≤ 2).
