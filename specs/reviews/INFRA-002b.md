# Review — INFRA-002b

## Verdict
REQUEST_CHANGES

## Findings

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| infra/terraform/cloudflare.tf:52-62 | HIGH | AC violation — Bot Fight Mode absent | AC-8 demande explicitement `Bot Fight Mode ON`. Le bloc `settings` ne contient que `ssl`, `security_level`, `challenge_ttl`, `brotli`, `always_use_https`. Aucune mention de `bot_fight_mode` (et il faut une resource séparée `cloudflare_bot_management` côté provider v4 free plan, ou setting `bot_fight_mode` selon plan). | Ajouter `cloudflare_bot_management` resource ou setting `bot_fight_mode = "on"` selon plan tier zone. |
| infra/terraform/cloudflare.tf:56 | HIGH | AC violation — valeur SSL invalide | `ssl = "full_strict"` n'est pas une valeur acceptée par `cloudflare_zone_settings_override` (provider v4 attend `"off" \| "flexible" \| "full" \| "strict"` où `"strict"` correspond au mode Full (strict)). `terraform apply` retournera `Error: expected ssl to be one of [...]`. AC-8 dit "TLS Full Strict". | `ssl = "strict"`. |
| infra/terraform/cloudflare.tf:47 | HIGH | Référence à ressource non déclarée dans cette PR | `route_name = google_cloud_run_v2_service.gateway.name` — la ressource `google_cloud_run_v2_service.gateway` est définie dans PR-a (`infra/terraform/cloud_run.tf`, branche `feat/INFRA-002a-terraform-gcp`), non encore mergée sur `main`. PR-b sur `main` standalone ne `terraform validate` PAS. | Documenter dépendance PR-a, OU squash-merge PR-a en premier (cf plan D-1 ordre imposé). PR-b non mergeable seule. |
| infra/terraform/variables.tf:25-33 | MED | Scope creep PR-a | Plan PR-b ligne 37 : ajout uniquement `cloudflare_account_id` + `cloudflare_api_token`. Or `billing_account` + `budget_email` (vars PR-a `budget.tf`) sont déclarés ici. Les vars `project_id`, `region`, `github_repo`, `domain` aussi appartiennent à PR-a. Out-of-scope refactor (`vertical-slice.md` "Touch only files listed"). | Retirer billing_account/budget_email/project_id/region/github_repo/domain de PR-b ; ces déclarations vivent dans PR-a `variables.tf`. PR-b ajoute seulement les 2 vars Cloudflare. |
| infra/terraform/main.tf:1-21 | MED | Scope creep PR-a | Plan PR-b ne mentionne pas `main.tf` dans Files to touch. Le bloc `provider "google"`, `provider "google-beta"` et `locals.labels` appartiennent à PR-a (plan ligne 22). PR-b doit seulement append `provider "cloudflare"`. | Réduire `main.tf` à `provider "cloudflare" { api_token = var.cloudflare_api_token }` (le reste vient de PR-a mergé). |
| infra/terraform/versions.tf:1-23 | MED | Scope creep PR-a | Plan PR-b ligne 36 : "ajout provider `cloudflare/cloudflare ~> 4`". Or PR-b recrée tout `versions.tf` (terraform block, required_version, backend GCS, google providers). Ces déclarations sont PR-a. | Si PR-a merge avant PR-b, ce fichier sera en conflit. Réduire diff PR-b à ajout du `cloudflare = {...}` dans `required_providers` existant. |
| infra/terraform/cloudflare.tf:124-135 | MED | Risque quota free tier | R2 du plan signale : Cloudflare Page Rules quota free = 3. PR-b crée 4 `cloudflare_page_rule`. Mitigation R2 (`cloudflare_ruleset http_request_dynamic_redirect`) non appliquée. `terraform apply` échouera sur la 4ème règle si zones en free plan. | Soit upgrader zones, soit basculer en `cloudflare_ruleset` `http_request_dynamic_redirect` (1 ruleset, N expressions). |
| infra/terraform/cloudflare.tf:29-35 | LOW | DNS proxied = true bloque la validation initiale Cloud Run domain mapping | Cloud Run domain mapping vérifie l'ownership via résolution DNS publique du CNAME. Avec `proxied=true`, Cloudflare renvoie ses IPs et masque `ghs.googlehosted.com`, ce qui empêche la validation initiale Google. Procédure standard : proxied=false durant validation, basculer à true ensuite. Aucune mention dans `bootstrap-gcp.md`. | Documenter procédure 2-temps dans bootstrap-gcp.md, OU `proxied = false` initial + manual flip post-validation. |
| infra/terraform/cloudflare.tf:78-81 | LOW | Action `challenge` au lieu de `block` | AC-8 autorise `block` OU `challenge`. Choix `challenge` valide spec. Note seulement : timeout=300s veut dire 5 min de challenge-passage après franchissement, alors que `challenge_ttl=1800` côté zone est de 30 min. Sémantique distincte, OK, mais pas commenté. | Ajouter commentaire WHY (sinon paraît être un magic number, cf `clean-code.md`). |
| docs/runbook/bootstrap-gcp.md:50-58 | LOW | Exemple `terraform.tfvars` avec placeholders | Le fichier d'exemple en clair documente la structure mais aucun secret réel n'apparaît. `.gitignore` ligne 17 (`*.tfvars`) couvre. OK, juste vérifier que l'humain ne commit jamais le fichier réel. | Aucune action ; rappel dans bootstrap step 4 OK. |
| infra/terraform/cloudflare.tf | LOW | Pas de tag/labels sur ressources Cloudflare | `locals.labels` défini dans main.tf mais non appliqué — Cloudflare provider ne supporte pas tags de toute façon. | N/A informatif. |

## Spec coverage (AC Cloudflare PR-b)

- AC-8 (CNAME archiviste.nocilia.fr proxied) : partiel — CNAME + proxied=true présent (`cloudflare.tf:29-35`), mais voir LOW sur validation initiale Cloud Run.
- AC-8 (TLS full_strict) : **KO** — valeur `"full_strict"` invalide pour provider v4 (`cloudflare.tf:56`). Doit être `"strict"`.
- AC-8 (Bot Fight Mode ON) : **KO** — absent du diff. Aucune resource Cloudflare ne l'active.
- AC-8 (Security Level medium) : OK (`cloudflare.tf:57`).
- AC-8 (Challenge Passage 1800s) : OK (`cloudflare.tf:58`).
- AC-8 (rate-limit 100 req/min/IP) : OK (`cloudflare.tf:65-82`), action `challenge` (valide alternative au `block`).
- AC-8 (4 Page Rules 301 redirects .com/.org/.eu/.net → .fr) : OK structurellement (`cloudflare.tf:85-135`), mais risque quota free tier (R2 du plan non mitigé).
- AC-14 (`/healthz` répond 200 derrière Cloudflare) : non vérifiable PR-b standalone (cf finding HIGH `route_name`).

## Property invariants

- Aucune propriété de `specs/properties.md` ne couvre la couche réseau/DNS. N/A pour ce PR.

## Security

- Pas de secret hardcodé dans le diff (`cloudflare_api_token` lu via `var.cloudflare_api_token` marqué `sensitive=true`, `variables.tf:42`). OK.
- Pas de `.tfvars` committé. `.gitignore:17` (`*.tfvars`) couvre. OK.
- TLS Full Strict réclamé (Cloudflare valide cert origine Cloud Run) — **valeur HCL incorrecte** = downgrade silencieux potentiel à default `flexible`/`full` selon zone si la string échappe à la validation provider. HIGH (cf premier finding SSL).
- Pas d'origin plain HTTP autorisé : `always_use_https = "on"` (`cloudflare.tf:60`). OK.
- Rate-limit 100/min/IP sur perimeter Cloudflare. Note : `.claude/rules/security.md` A04 dit "60 req/min/IP default" mais la spec AC-8 override explicitement à 100. Spec gagne, pas un finding.
- WAF rules : seul Bot Fight Mode + Security Level Medium réclamés AC-8. Bot Fight Mode **absent** = finding HIGH (cf supra). Pas de WAF custom au-delà = non-goal explicite spec ligne 49.
- Token Cloudflare hors Secret Manager GCP : décision spec ligne 122 (provider credential, pas runtime). `bootstrap-gcp.md` step 7 documente. OK.

## Format check

- `terraform fmt -check -recursive infra/` : **non exécuté** — `terraform` CLI absent du PATH de l'environnement de review. Sur revue visuelle, observation : `cloudflare.tf:60` `always_use_https = "on"` n'est pas aligné avec les autres clés du bloc settings (`ssl`, `security_level`, `challenge_ttl`, `brotli` ont `=` aligné colonne 20, `always_use_https` étire l'alignement). `terraform fmt` ré-alignera tout le bloc. Idem `cloudflare.tf:69` (`description` n'est pas aligné avec `threshold`/`period`). Probablement `fmt -check` retourne non-zero. À confirmer localement avant merge.

## Out-of-scope changes

- `infra/terraform/main.tf` créé en entier (devrait être PR-a).
- `infra/terraform/versions.tf` créé en entier (devrait être PR-a).
- `infra/terraform/variables.tf` contient 6 vars non-Cloudflare (`project_id`, `region`, `github_repo`, `domain`, `billing_account`, `budget_email`) qui appartiennent à PR-a.
- Justification possible : worktree PR-b standalone contre `origin/main` doit pouvoir `terraform init`. Mais le plan D-1 explicite "merge ordered a → b → c → d" — donc rebase de PR-b sur PR-a une fois mergée doit produire diff propre. À squash/rebase au moment de ship.

## Rules applied

- `clean-code.md` : 1 magic number non commenté (timeout=300 rate-limit). LOW.
- `vertical-slice.md` : scope creep PR-a via main.tf/versions.tf/variables.tf. MED. Diff HCL exempt 300 LOC limit.
- `no-workaround.md` : risque R2 (page rules quota) non traité ni loggé en blocker. À documenter ou mitiger avant apply.
- `secret-hygiene.md` : OK (token sensitive, tfvars gitignored, exemple placeholder only).
- `security.md` : Bot Fight Mode AC absent + valeur SSL invalide = downgrade silencieux. HIGH.

---

## Rapport synthétique

3 findings HIGH bloquants :

1. `ssl = "full_strict"` (cloudflare.tf:56) — valeur invalide provider v4, doit être `"strict"`. Risque downgrade TLS silencieux côté Cloudflare → Cloud Run. AC-8 violé.
2. Bot Fight Mode absent du diff — AC-8 violé explicitement.
3. `route_name = google_cloud_run_v2_service.gateway.name` (cloudflare.tf:47) référence ressource PR-a → PR-b non validable contre `origin/main` seule. Acceptable si ordre merge a→b respecté (cf plan D-1), mais doit être explicité.

3 findings MED scope creep : `main.tf`, `versions.tf`, et 6 vars dans `variables.tf` appartiennent à PR-a. À rebase post-merge PR-a.

2 LOW : DNS proxied=true bloque validation initiale Cloud Run domain mapping (procédure manuelle non documentée), magic number timeout=300 sur rate-limit.

`terraform fmt -check` non exécutable (CLI absent), revue visuelle suggère alignement non conforme (`always_use_https` ligne 60, `description` ligne 69). À confirmer localement.

Rapport écrit : `D:\Projet-perso\archiviste-nocilia\specs\reviews\INFRA-002b.md`
