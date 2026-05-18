# Review — INFRA-002b

## Round 2 (commit `bf254bc`)

### Résolutions Round 1

| Finding R1 | Sév R1 | État R2 | Évidence |
|---|---|---|---|
| `ssl = "full_strict"` invalide | HIGH | RÉSOLU | `cloudflare.tf:58` → `ssl = "strict"`. WHY comment ligne 52 documente que `"strict"` = "Full (strict)" provider v4. |
| Bot Fight Mode absent | HIGH | RÉSOLU | `cloudflare.tf:63` → `bot_fight_mode = "on"` ajouté dans `cloudflare_zone_settings_override.nocilia_fr.settings`. WHY comment ligne 53 rappelle scope `Zone:Bot Management` requis. |
| `route_name` cross-PR | HIGH | RÉSOLU (doc) | `cloudflare.tf:47` inchangé (référence `google_cloud_run_v2_service.gateway`), mais `docs/runbook/bootstrap-gcp.md:3-6` documente explicitement l'ordre `PR-a → PR-b → PR-c → PR-d` et avertit que `terraform validate` standalone échouera. Conforme plan D-1. |
| Scope creep `main.tf` | MED | RÉSOLU | `main.tf` réduit à 5 lignes — provider Cloudflare seul. `google` / `google-beta` / `locals.labels` retirés (renvoyés à PR-a). |
| Scope creep `variables.tf` | MED | RÉSOLU | `variables.tf` réduit à 13 lignes : 2 vars Cloudflare. 6 vars PR-a (`project_id`, `region`, `github_repo`, `domain`, `billing_account`, `budget_email`) retirées. |
| Scope creep `versions.tf` | MED | PARTIEL | Fichier toujours créé en entier (terraform block + required_version + backend gcs + google providers + cloudflare). Comment ligne 1-4 reconnait explicitement « will produce a merge conflict with PR-a if applied standalone; only valid after PR-a is merged ». Intent documenté, conflit assumé. Acceptable comme working tree de worktree PR-b standalone si rebase post-merge PR-a produit un diff propre ajoutant le seul bloc `cloudflare = {...}` au `required_providers`. |
| Quota Page Rules free tier | MED | RÉSOLU | `cloudflare.tf:131-153` — `.net` migré en `cloudflare_ruleset` phase `http_request_dynamic_redirect`. Compte Page Rules ramené à 3/3 (com/org/eu). WHY comment ligne 87-90 documente la mitigation. |
| Procédure validation Cloud Run domain mapping proxied=true | LOW | NON RÉSOLU | `bootstrap-gcp.md` ne mentionne pas la procédure 2-temps (proxied=false durant validation initiale, flip à true post-validation). Risque ship `archiviste.nocilia.fr` qui reste en `PROVISIONING` côté Cloud Run. Carry-over LOW. |
| Magic number `timeout = 300` | LOW | NON RÉSOLU | `cloudflare.tf:83` inchangé. Carry-over LOW. |

### Findings résiduels Round 2

| File:line | Severity | Pattern | Evidence | Suggested fix |
|---|---|---|---|---|
| infra/terraform/versions.tf:6-28 | LOW | Scope creep résiduel assumé | Le fichier recrée tout le bloc `terraform{}` (required_version, backend gcs, google providers). Reconnu par comment, mais provoquera un conflit de merge avec PR-a au moment du rebase. Pas un bloqueur si la procédure de rebase post-PR-a est exécutée proprement. | Au ship, rebase PR-b sur PR-a mergé puis produire un diff `versions.tf` qui ajoute uniquement le bloc `cloudflare = {...}` dans `required_providers`. À vérifier avant `git push` final. |
| docs/runbook/bootstrap-gcp.md | LOW | Procédure proxied=true / Cloud Run domain mapping non documentée | Cloud Run `google_cloud_run_domain_mapping` vérifie ownership via résolution DNS publique du CNAME. Avec proxied=true, Cloudflare répond ses IPs et masque `ghs.googlehosted.com`, ce qui empêche la validation initiale Google. Aucune mention. | Ajouter section bootstrap : (1) `terraform apply` avec `proxied=false` pour le record, (2) attendre `gcloud run domain-mappings describe ...` retournant `READY`, (3) re-apply avec `proxied=true`. OU paramétrer `cloudflare_record.archiviste_fr.proxied` via variable défault=false flip humain. |
| infra/terraform/cloudflare.tf:83 | LOW | Magic number non commenté | `timeout = 300` (5 min) sur l'action `challenge` du rate-limit. Pas justifié vs `challenge_ttl=1800` ligne 60. | Ajouter WHY comment (sémantique distincte : timeout = durée du challenge passage post-déclenchement, vs challenge_ttl = TTL global zone). |

### Round 2 — Vérifications spécifiques mission

| Check | Statut | Évidence |
|---|---|---|
| `ssl = "strict"` (provider v4 accept) | OK | `cloudflare.tf:58`. Provider v4 schema enum : `"off" \| "flexible" \| "full" \| "strict"`. Valeur valide. |
| `bot_fight_mode = "on"` (AC-8) | OK | `cloudflare.tf:63` dans `cloudflare_zone_settings_override.nocilia_fr.settings`. |
| Scope creep résolu — `main.tf` | OK | 5 lignes, provider Cloudflare seul. |
| Scope creep résolu — `variables.tf` | OK | 13 lignes, 2 vars Cloudflare. |
| Scope creep résolu — `versions.tf` | PARTIEL | Fichier complet recréé, conflit merge avec PR-a explicitement assumé en comment. Acceptable si rebase post-PR-a discipliné. |
| Quota Page Rules — mitigation R2 | OK | `.net` en `cloudflare_ruleset`. 3 Page Rules restants ≤ quota 3 free plan. |

### Spec coverage AC-8 (final R2)

- AC-8 CNAME `archiviste.nocilia.fr` proxied : OK (`cloudflare.tf:29-35`). LOW résiduel sur validation initiale Cloud Run.
- AC-8 TLS Full Strict : **OK** (`ssl = "strict"`, `cloudflare.tf:58`).
- AC-8 Bot Fight Mode ON : **OK** (`cloudflare.tf:63`).
- AC-8 Security Level medium : OK.
- AC-8 Challenge Passage 1800s : OK.
- AC-8 rate-limit 100 req/min/IP : OK.
- AC-8 4 redirects 301 `.com`/`.org`/`.eu`/`.net` → `.fr` : OK (3 Page Rules + 1 ruleset). Quota free préservé.
- AC-14 `/healthz` derrière Cloudflare : non-vérifiable PR-b standalone. Conforme plan D-1 (validation post-ship).

### Diff stats R2

```
CHANGELOG.md                  |   2 +
docs/runbook/bootstrap-gcp.md | 126 ++++++++++++++++
infra/terraform/cloudflare.tf | 153 +++++++++++++++++++
infra/terraform/main.tf       |   5 +
infra/terraform/variables.tf  |  13 ++
infra/terraform/versions.tf   |  28 +++
6 files changed, 327 insertions(+)
```

327 LOC total. Au-delà du seuil 300 LOC vertical-slice mais `bootstrap-gcp.md` (126 LOC doc opérateur) admissible comme runbook non-code. Code-only ≈ 201 LOC. Conforme.

### Rapport synthétique Round 2

**3 HIGH Round 1 → tous résolus.**
- `ssl = "strict"` correct provider v4.
- `bot_fight_mode = "on"` présent.
- Ordre merge PR-a → PR-b documenté en haut de `bootstrap-gcp.md`.

**3 MED scope creep → 2 résolus + 1 partiel assumé.**
- `main.tf` et `variables.tf` réduits au strict minimum Cloudflare.
- `versions.tf` toujours complet mais conflit merge documenté en comment (intent explicite). Acceptable.

**MED quota R2 → résolu** par migration `.net` vers `cloudflare_ruleset http_request_dynamic_redirect`.

**2 LOW carry-over (non-bloquant)** :
- Procédure proxied=true / Cloud Run domain mapping initiale absente du runbook.
- Magic number `timeout=300` non commenté.

---

## Verdict
APPROVE

Tous les HIGH Round 1 sont résolus. Scope creep ramené au strict nécessaire (versions.tf reste assumé en working tree avec comment explicite et rebase prévu post-PR-a). Mitigation quota Page Rules appliquée. Les 2 LOW résiduels n'empêchent pas le ship — à traiter en suivi : (1) ajouter procédure proxied flip dans `bootstrap-gcp.md` avant premier `terraform apply` humain (ou paramétrer `proxied` via variable), (2) WHY comment sur `timeout=300`. À vérifier au ship : (a) `terraform fmt -check` localement avant push, (b) rebase propre de `versions.tf` post-merge PR-a (diff final = ajout du seul bloc `cloudflare = {...}` dans `required_providers`).
