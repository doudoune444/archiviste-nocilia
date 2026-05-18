# Plan — INFRA-002 Deploy GCP beta `archiviste.nocilia.fr`

## Goal
Premier ship public sur `https://archiviste.nocilia.fr` (europe-west9) via Terraform (Cloud Run + Cloud SQL + GCS + Secret Manager + IAM/WIF + Cloudflare + budget) + GHA `deploy.yml` canary/smoke/auto-rollback + swap embedder Python BGE-M3 → `mistral-embed`.

## Acceptance criteria recap
Voir `specs/acceptance/INFRA-002.md` AC-1 à AC-14 (verbatim, non recopiés ici — 14 items, ne pas paraphraser).

## Split forcé par taille (D-1, spec ≥ 600 LOC HCL)
1 spec, **4 PRs séquentiels**, chacun ≤ 300 LOC, merge ordonné a → b → c → d sur `main`. Aucun PR mergeable hors ordre (sinon ship cassé).

- **PR a — Terraform core GCP** (`feat/INFRA-002a-terraform-gcp`) : state backend + Cloud Run × 2 + Cloud SQL + GCS + Secret Manager + IAM (2 SA) + WIF + Artifact Registry + budget. ~260 LOC HCL.
- **PR b — Terraform Cloudflare** (`feat/INFRA-002b-cloudflare`) : zone (data), DNS `archiviste.nocilia.fr`, domain mapping Cloud Run, TLS Full Strict, Bot Fight Mode, rate-limit rule, 4 Page Rules redirects. ~140 LOC HCL.
- **PR c — GHA `deploy.yml` + runbook** : workflow WIF + build + push AR + deploy canary + smoke + auto-rollback. Finalisation `docs/runbook/rollback.md`. ~150 LOC YAML + 30 LOC MD.
- **PR d — Embedder swap BGE-M3 → mistral-embed** : nouvelle implé `Embedder`, tests, drop deps HF runtime, Dockerfile workers slim. ~80 LOC Python + deps.

## Files to touch (par PR)

### PR a — Terraform core
- `infra/terraform/versions.tf` — `terraform {}` + `required_providers` (google ~> 6, google-beta ~> 6) + backend GCS
- `infra/terraform/variables.tf` — `project_id`, `region = "europe-west9"`, `github_repo`, `domain`, `budget_email`
- `infra/terraform/main.tf` — providers, locals (`labels`)
- `infra/terraform/artifact_registry.tf` — repo `archiviste` Docker europe-west9
- `infra/terraform/secrets.tf` — secret `MISTRAL_API_KEY` (sans version : bootstrap manuel runbook)
- `infra/terraform/cloud_sql.tf` — instance `archiviste-db` Postgres 16 `db-f1-micro` 10 GB, `database_flags { name = "cloudsql.enable_pgvector" value = "on" }` OU bootstrap `CREATE EXTENSION` via `google_sql_user` + `null_resource` `gcloud sql connect` (architect tranche au /implement après vérif support flag pgvector européen). DB `archiviste`, PITR ON 7j.
- `infra/terraform/gcs.tf` — bucket `archiviste-conversations` europe-west9, `uniform_bucket_level_access = true`, `public_access_prevention = "enforced"`, lifecycle `Delete age=30`
- `infra/terraform/iam.tf` — 2 SA (`gha-deploy@`, `archiviste-runtime@`) + 5 rôles deploy + 2 rôles runtime project-wide + 1 `google_storage_bucket_iam_member` bucket-scoped `storage.objectAdmin`
- `infra/terraform/iam_wif.tf` — `google_iam_workload_identity_pool` + `google_iam_workload_identity_pool_provider` GitHub OIDC + binding `roles/iam.workloadIdentityUser` avec CEL `assertion.repository == 'doudoune444/archiviste-nocilia' && assertion.ref == 'refs/heads/main'`
- `infra/terraform/cloud_run.tf` — 2 services (`archiviste-gateway` ingress=all 256Mi, `archiviste-workers` ingress=internal 512Mi), `min_instances=0`, annotation `run.googleapis.com/cloudsql-instances`, env `INSTANCE_CONNECTION_NAME`, env `LLM_API_KEY` via `value_source.secret_key_ref` (workers only), env `GCS_BUCKET`, env `DATABASE_URL` (socket Unix `/cloudsql/<conn>`), image placeholder pointing AR `:latest` (overridden by GHA `gcloud run deploy --image`)
- `infra/terraform/budget.tf` — `google_billing_budget` `archiviste-beta-monthly` 50 EUR notif email owner @ 100 %
- `infra/terraform/outputs.tf` — `gateway_url`, `workers_url`, `instance_connection_name`, `wif_provider`, `gha_deploy_sa_email`, `artifact_registry_repo`
- `docs/runbook/bootstrap-gcp.md` — **nouveau** (D-3) : one-shot pré-conditions (`gcloud auth`, API enablement list, création state bucket initial, `terraform init` + premier `apply`, version Secret Manager bootstrap manuel `gcloud secrets versions add MISTRAL_API_KEY --data-file=-`)
- `CHANGELOG.md` — entrée Infra

### PR b — Terraform Cloudflare
- `infra/terraform/versions.tf` — ajout provider `cloudflare/cloudflare ~> 4`
- `infra/terraform/variables.tf` — `cloudflare_account_id`, `cloudflare_api_token` (sensitive)
- `infra/terraform/cloudflare.tf` — `data "cloudflare_zone"` × 5 (fr/com/org/eu/net), `cloudflare_record` CNAME `archiviste` → `ghs.googlehosted.com`, `google_cloud_run_domain_mapping` (D-4 primary path), `cloudflare_zone_settings_override` (ssl=`full_strict`, security_level=`medium`, challenge_ttl=`1800`, bot_fight_mode=`on`), `cloudflare_rate_limit` 100 req/min, `cloudflare_page_rule` × 4 (`.com`/`.org`/`.eu`/`.net` 301 → `https://archiviste.nocilia.fr/$1`)
- `docs/runbook/bootstrap-gcp.md` — append section Cloudflare token (out-of-band, scope `Zone:Edit`+`DNS:Edit`+`Page Rules:Edit`+`Bot Management:Edit`)
- `CHANGELOG.md` — append

### PR c — GHA deploy + runbook
- `.github/workflows/deploy.yml` — **nouveau** :
  - trigger `push` branches `[main]`
  - permissions `id-token: write`, `contents: read`
  - steps : checkout → `google-github-actions/auth@v2` (WIF, `workload_identity_provider`, `service_account = gha-deploy@…`, **pas** de `credentials_json`) → `gcloud auth configure-docker europe-west9-docker.pkg.dev` → docker buildx build + push gateway + workers `:<git_sha>` → `gcloud run deploy archiviste-gateway --image …:<sha> --no-traffic --tag canary --region europe-west9` (idem workers) → smoke `curl -sf $(gcloud run revisions describe <new>-canary --format='value(status.url)')/healthz` (bypass Cloudflare pour éviter DNS race) → si OK : `gcloud run services update-traffic --to-revisions=<new>=100` × 2 → si KO : `gcloud run services update-traffic --to-revisions=PREVIOUS=100` × 2 + `exit 1`
- `docs/runbook/rollback.md` — finalisation (déjà 3 cmds + PITR présent ; vérifier section Détection + workflow auto-rollback xref)
- `CHANGELOG.md` — append

### PR d — Embedder swap
- `workers/src/archiviste_workers/embedder.py` — réécriture : `class Embedder` wraps `langchain_mistralai.MistralAIEmbeddings(model="mistral-embed", api_key=settings.llm_api_key)`, conserve `EMBEDDING_DIM = 1024`, conserve assertion, conserve `encode_batch(texts, batch_size) -> list[list[float]]`. Constant `DEFAULT_MODEL_NAME = "mistral-embed"`. Commentaire "Fallback BAAI/bge-m3 self-host = V2 (cf vision.md Q7)".
- `workers/src/archiviste_workers/settings.py` — aucun changement champ ; commentaire explicite que `LLM_API_KEY` = clé Mistral partagée LLM + embed (vision Q7). Pas de nouveau `mistral_api_key`.
- `workers/pyproject.toml` — **drop** `sentence-transformers>=3.3`, `transformers>=4.45` du runtime ; déplacer en `[project.optional-dependencies] embedder-fallback = [...]` (documenté, non installé prod)
- `workers/tests/test_embedder.py` — réécriture : mock HTTP via `pytest-httpserver` retournant payload Mistral `{"data": [{"embedding": [0.0]*1024}, …]}`, vérifie `len == 1024`, vérifie batch size param respecté
- `workers/tests/test_embedder_properties.py` — adapter : conserve hypothesis property `len(v) == 1024 for v in encode_batch(...)`, mock client Mistral au niveau langchain
- `infra/docker/workers.Dockerfile` — inchangé syntaxiquement, mais image plus légère via pyproject diff (gain ~2 GB cold start)
- `.github/workflows/ci.yml` — supprimer step `restore HF Hub cache` (cache devient inutile sans `sentence-transformers`)
- `CHANGELOG.md` — append

## Test strategy

### PR a (Terraform core)
- **Contract** : `terraform fmt -recursive -check infra/terraform/` exit 0 + `terraform -chdir=infra/terraform validate` exit 0 (lancé en pré-commit + ajouter step CI optionnel à `ci.yml` ou nouveau `.github/workflows/terraform.yml` — décision architect : **PAS de workflow terraform.yml V1** (non-goal explicite spec ligne 52), valider en local uniquement.
- **Contract grep** (AC-11 négatif anticipé + AC-7 positif + D-7) :
  - `! grep -rE 'google_service_account_key' infra/terraform/` (D-7 garde-fou)
  - `grep -F "assertion.repository == 'doudoune444/archiviste-nocilia' && assertion.ref == 'refs/heads/main'" infra/terraform/iam_wif.tf`
- **Intégration manuelle** post-`terraform apply` : `gcloud run services describe`, `gcloud sql instances describe`, `gsutil ls -L gs://archiviste-conversations`, `gcloud secrets list`, `gcloud iam service-accounts get-iam-policy`, `gcloud billing budgets list`.

### PR b (Cloudflare)
- **Contract** : `terraform validate` exit 0.
- **Intégration manuelle** post-apply : `dig archiviste.nocilia.fr +short`, vérifier proxy CF (cloudflare IPs), `curl -sI https://archiviste.nocilia.com/` → 301 vers `.fr`.

### PR c (GHA deploy)
- **Contract grep AC-11** :
  - `grep -E 'workload_identity_provider|service_account' .github/workflows/deploy.yml` matche
  - `! grep -E 'credentials_json|GCP_SA_KEY' .github/workflows/deploy.yml`
- **Lint** : `actionlint` step déjà dans `ci.yml` → couvre `deploy.yml` automatiquement.
- **Intégration end-to-end** : premier run sur merge `main` (= ship public). Test négatif AC-12 : 1 commit volontairement cassant sur branche temporaire mergée → workflow déclenche rollback PREVIOUS=100 + exit 1 (documenté runbook, non rejoué CI permanente).

### PR d (Embedder)
- **Unit** : `tests/test_embedder.py` — mock Mistral via `pytest-httpserver`, asserte dim 1024, asserte batch propagé, asserte `RuntimeError` si dim mismatch (test négatif).
- **Property** : `tests/test_embedder_properties.py` — hypothesis sur `texts: list[str]` non-vide → `len(v) == 1024 for v in result`, `len(result) == len(texts)`.
- **CI** : `pytest` workers existant suffit (lance les 2 tests réécrits).
- **Live opt-in** (existant) : marker `pytest -m live` skipped par défaut, appelle Mistral réel si `LLM_API_KEY` valide en env.
- **Pas de re-indexage corpus** (D-6 + vision Q7 : dim identique 1024, swap config only).

## Implementation steps (ordered)

1. **PR a — Terraform core** (≤ 300 LOC HCL + bootstrap runbook MD)
   1. `versions.tf` (backend GCS, providers).
   2. `variables.tf` + `main.tf` (providers, locals).
   3. `artifact_registry.tf`, `secrets.tf` (sans version), `gcs.tf`, `cloud_sql.tf`.
   4. `iam.tf` + `iam_wif.tf` (CEL exacte, grep test).
   5. `cloud_run.tf` (2 services, sidecar via annotation, env from secret).
   6. `budget.tf`, `outputs.tf`.
   7. `docs/runbook/bootstrap-gcp.md` (one-shot pré-conditions, version secret bootstrap manuel).
   8. `terraform fmt -recursive` + `terraform validate` localement.
   9. CHANGELOG. PR ouvert, revue humaine, **operator lance `terraform apply` post-merge** (pas via CI V1).

2. **PR b — Terraform Cloudflare** (≤ 200 LOC HCL)
   1. Ajout provider Cloudflare + 2 vars (token sensitive).
   2. `cloudflare.tf` complet (5 zones data, DNS, page rules, zone settings, rate-limit).
   3. `google_cloud_run_domain_mapping` archiviste.nocilia.fr (primary path D-4). Si bloqueur preview/GA europe-west9 au /implement → bascule fallback (CNAME direct `*.run.app`, doc Host rewrite). `no-workaround.md` : si bloqueur Cloudflare provider non-trivial → `docs/blockers.md`.
   4. `validate` + apply manuel operator.
   5. CHANGELOG.

3. **PR c — GHA deploy.yml + runbook finalisation** (≤ 200 LOC YAML/MD)
   1. `deploy.yml` : trigger, permissions, auth WIF.
   2. Steps build + push AR par image (gateway/workers, BuildKit).
   3. `gcloud run deploy --no-traffic --tag canary` × 2.
   4. Smoke test direct `*.run.app` (bypass Cloudflare DNS race).
   5. Promote OU rollback PREVIOUS=100 × 2.
   6. Finaliser `docs/runbook/rollback.md` (xref `deploy.yml`, section Détection complète).
   7. CHANGELOG.
   8. Validation end-to-end : merge déclenche premier deploy réel (= AC-14).

4. **PR d — Embedder swap** (≤ 100 LOC Python)
   1. Réécriture `embedder.py` (`MistralAIEmbeddings`, dim assertion conservée).
   2. Réécriture `tests/test_embedder.py` (pytest-httpserver mock).
   3. Adapt `tests/test_embedder_properties.py` (hypothesis avec mock).
   4. `pyproject.toml` : drop `sentence-transformers`, `transformers` runtime ; ajout `extras.embedder-fallback`.
   5. `.github/workflows/ci.yml` : drop step HF cache.
   6. `pytest` local vert → `ruff` + `mypy --strict` verts.
   7. CHANGELOG.

## Risks / open questions

- **R1 — Cloud Run gen2 custom domain GA europe-west9** : `google_cloud_run_domain_mapping` peut être beta-only dans europe-west9 au moment d'apply. Plan PR b a fallback CNAME direct `*.run.app` (D-4). Décision arbitrée au /implement.
- **R2 — Cloudflare Page Rules quota free tier = 3** (spec demande 4). Mitigation : Cloudflare Redirect Rules (remplacement moderne Page Rules, quota plus large) via `cloudflare_ruleset` `http_request_dynamic_redirect`. À valider PR b.
- **R3 — pgvector activation Cloud SQL europe-west9** : flag `cloudsql.enable_pgvector` selon support région ; sinon bootstrap manuel `CREATE EXTENSION` via `null_resource` + `gcloud sql connect`. Architect tranche au /implement après check console.
- **R4 — Operator workflow `terraform apply`** : spec ligne 52 non-goal `terraform.yml` CI. Donc apply = humain en local après merge PR a/b. `bootstrap-gcp.md` documente. Pas de drift detect V1.
- **R5 — Cloudflare API token storage** : hors Secret Manager GCP (provider Cloudflare, pas Cloud Run runtime). Token en variable Terraform locale chiffrée OU GitHub Actions secret si CI Terraform. V1 = `.tfvars` local gitignored + GitHub Actions secret `CLOUDFLARE_API_TOKEN` (reservé pour V2 si CI plan adopté).
- **R6 — Smoke test depuis runner GHA contre `archiviste.nocilia.fr`** : DNS propagation race (TTL Cloudflare initial). Plan bypass : smoke direct `*.run.app` du tagged revision canary. AC-14 vérifié manuellement post-deploy.
- **R7 — Drop `sentence-transformers` casse tests existants** : `tests/test_embedder*.py` à réécrire intégralement (PR d). Vérifier aucun autre code path n'importe `SentenceTransformer` (grep confirmed : seul `embedder.py`).
- **R8 — Secret `MISTRAL_API_KEY` bootstrap** : Terraform crée la ressource secret mais PAS la version. Premier `terraform apply` PR a → operator doit `gcloud secrets versions add MISTRAL_API_KEY --data-file=-` AVANT premier deploy GHA, sinon workers boot KO (ImportError Mistral client). Documenté `bootstrap-gcp.md`.

## Out of scope (verbatim spec non-goals)

- Redis/Memorystore, VPC connector, Serverless VPC Access (V2 SEC-002).
- Cost-guard app-level, fallback chain LLM (V2 SEC-010).
- Observabilité étendue : uptime checks, log-based metrics, alert policies, OTel→Cloud Logging (V2 OBS-001).
- Security headers HSTS/CSP/nosniff/Referrer-Policy (SEC-003 post-INFRA-002).
- Load tests k6 (OPS-001).
- Turnstile, WAF custom au-delà des rules AC-8.
- Split runtime SA workers vs gateway (V2 SEC-001).
- Auth app : `user_tier="anonymous"` hardcodé reste.
- Workflow Terraform CI (`terraform.yml` plan-on-PR) — déféré.
- Scripts down migration (PITR Cloud SQL = seul filet V1).
- Re-indexation corpus (V2 ING-016).
- Cloud Armor.
- Domain mapping Cloud Run sur workers (workers ingress=internal).
