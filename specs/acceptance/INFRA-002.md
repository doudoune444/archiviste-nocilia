# INFRA-002 — Deploy GCP beta : premier ship public `archiviste.nocilia.fr`

## Contexte

L'application est curl-able en local mais n'a jamais été exposée publiquement. Ce ticket livre le premier déploiement sur GCP (région `europe-west9`, Paris) avec stack full managed — Cloud Run + Cloud SQL + GCS + Secret Manager + Cloudflare — via Terraform et un workflow GHA `deploy.yml` automatisé canary 0 % → smoke → promote 100 % ou auto-rollback. Il réactive ADR-0003 (`infra/terraform/` source de vérité) et conditionne la suite de l'ordre d'attaque V1 beta (SEC-003, GEN-003, GEN-004, UI-002, SEC-001, GEN-005, OPS-001).

## Critères d'acceptation

- AC-1 : `infra/terraform/` contient une racine Terraform fonctionnelle (`main.tf`, `variables.tf`, `outputs.tf`, `versions.tf`) avec backend GCS (bucket d'état + state locking) configuré en `europe-west9`. `terraform fmt -recursive -check` et `terraform validate` passent sans erreur sur l'arbre.
- AC-2 : Terraform provisionne 2 services Cloud Run distincts dans `europe-west9` :
  - `archiviste-gateway` (CPU/mémoire `256Mi`, scale-to-zero `min_instances = 0`, ingress `all`).
  - `archiviste-workers` (mémoire `512Mi`, scale-to-zero `min_instances = 0`, ingress `internal`).
  Chaque service consomme une image Artifact Registry tagguée par `git sha`.
- AC-3 : Terraform provisionne 1 instance Cloud SQL Postgres 16 `db-f1-micro` 10 GB nommée `archiviste-db` avec extension `vector` activée (via `database_flags` ou bootstrap SQL post-create). L'accès depuis Cloud Run passe par un **sidecar Cloud SQL Auth Proxy en socket Unix** (`/cloudsql/<connection_name>`) déclaré sur les deux services. Aucun VPC connector n'est créé.
- AC-4 : Terraform provisionne le bucket GCS `archiviste-conversations` avec `uniform_bucket_level_access = true`, `public_access_prevention = "enforced"`, et une lifecycle rule `Delete` sur `age = 30` jours.
- AC-5 : Terraform provisionne 1 secret Secret Manager nommé `MISTRAL_API_KEY` (1 secret partagé LLM + embeddings, même provider Mistral). La version courante est injectée à `archiviste-workers` via `env.value_source.secret_key_ref`. Aucun autre secret applicatif n'est créé par ce ticket.
- AC-6 : Terraform provisionne 2 service accounts :
  - `gha-deploy@<project>.iam.gserviceaccount.com` avec exactement les rôles `roles/run.admin`, `roles/artifactregistry.writer`, `roles/cloudsql.client`, `roles/secretmanager.secretAccessor`, `roles/iam.serviceAccountUser`.
  - `archiviste-runtime@<project>.iam.gserviceaccount.com` (runtime partagé gateway + workers V1) avec exactement les rôles `roles/cloudsql.client`, `roles/secretmanager.secretAccessor`, et `roles/storage.objectAdmin` **bucket-scoped au bucket `archiviste-conversations` uniquement** (pas project-wide).
- AC-7 : Terraform provisionne un Workload Identity Pool + Provider GitHub-OIDC. Le binding `roles/iam.workloadIdentityUser` sur `gha-deploy@` exige la condition CEL exacte :
  `assertion.repository == 'doudoune444/archiviste-nocilia' && assertion.ref == 'refs/heads/main'`.
  Aucune clé JSON de service account n'est générée ni stockée.
- AC-8 : Terraform provisionne une zone Cloudflare pour le domaine `nocilia.fr` (ou réutilise la zone existante via `data` source) avec :
  - Enregistrement DNS `archiviste.nocilia.fr` pointant vers le custom domain Cloud Run gateway (CNAME `ghs.googlehosted.com` ou équivalent via `google_cloud_run_domain_mapping`), proxy Cloudflare ON.
  - Mode TLS `full_strict`.
  - Bot Fight Mode ON, Security Level `medium`, Challenge Passage `1800` secondes (30 min).
  - 1 rate-limiting rule : `100 req / 1 min / IP` sur le hostname `archiviste.nocilia.fr`, action `block` ou `challenge`.
  - 4 Page Rules 301 redirects : `archiviste.nocilia.com/*`, `archiviste.nocilia.org/*`, `archiviste.nocilia.eu/*`, `archiviste.nocilia.net/*` → `https://archiviste.nocilia.fr/$1`.
- AC-9 : Terraform provisionne 1 budget GCP nommé `archiviste-beta-monthly` à **50 EUR** sur le projet, avec une notification email à l'adresse propriétaire dès franchissement de 100 % du seuil.
- AC-10 : Le workers Python utilise Mistral `mistral-embed` (dim 1024) comme embedder par défaut au lieu de `BAAI/bge-m3`. La dimension stockée en colonne `vector(1024)` reste identique, **aucune migration SQL n'est ajoutée**, aucun re-indexage du corpus n'est requis. Le wrapper embedder lit la clé via `MISTRAL_API_KEY` (env injectée depuis Secret Manager en prod, `.env` en local). Le fallback `BAAI/bge-m3` self-host reste documenté en commentaire / README workers mais n'est pas activé en V1.
- AC-11 : `.github/workflows/deploy.yml` existe et se déclenche sur `push` vers `main`. Le workflow s'authentifie à GCP via WIF (`google-github-actions/auth@v2` avec `workload_identity_provider` + `service_account = gha-deploy@...`), sans aucun `credentials_json` ni secret JSON.
- AC-12 : Le workflow `deploy.yml` exécute, dans cet ordre, et échoue le job si une étape échoue :
  1. Build images gateway + workers (Docker BuildKit).
  2. Push vers Artifact Registry `europe-west9-docker.pkg.dev/<project>/archiviste/{gateway,workers}:<git_sha>`.
  3. `gcloud run deploy` chaque service avec `--no-traffic` (nouvelle révision à 0 % de trafic).
  4. Smoke test : `curl -sf https://archiviste.nocilia.fr/healthz` (via tag de révision canary) renvoie HTTP 2xx.
  5. Si smoke OK : `gcloud run services update-traffic --to-latest --to-revisions=<new>=100` sur les deux services.
  6. Si smoke KO : `gcloud run services update-traffic --to-revisions=PREVIOUS=100` sur les deux services, puis `exit 1` pour faire échouer le run GHA.
- AC-13 : `docs/runbook/rollback.md` existe (déjà scaffold) et documente exactement les 3 commandes `gcloud` du rollback manuel : (1) `gcloud run revisions list`, (2) `gcloud run services update-traffic --to-revisions=<PREV>=100`, (3) `curl https://archiviste.nocilia.fr/healthz` de vérification. La section DB rollback référence le PITR Cloud SQL (rétention 7j auto sur `db-f1-micro`).
- AC-14 : Post-merge et exécution réussie du workflow `deploy.yml` sur `main`, `https://archiviste.nocilia.fr/healthz` répond HTTP 200 depuis l'extérieur (Cloudflare + Cloud Run gateway en place, TLS valide, redirect `.com`/`.org`/`.eu`/`.net` → `.fr` actifs).

## Non-goals

- Pas de Redis / Memorystore. Pas de VPC connector. Pas de Serverless VPC Access. (V2 SEC-002.)
- Pas de cost-guard app-level, pas de fallback chain LLM Claude → Mistral → Gemini. (V2 SEC-010.)
- Pas d'observabilité complète : pas d'uptime checks Cloud Monitoring, pas de log-based metrics, pas d'alert policies multiples au-delà du budget. Pas d'export OTel → Cloud Logging. (V2 OBS-001.)
- Pas de security headers HSTS / CSP / X-Content-Type-Options / Referrer-Policy. (Ticket séparé SEC-003 immédiatement post-INFRA-002.)
- Pas de load tests k6 100/500 users. (OPS-001.)
- Pas de Cloudflare Turnstile, pas de WAF custom au-delà des rules listées AC-8.
- Pas de runtime SA split workers vs gateway (V1 = runtime SA partagé `archiviste-runtime@`). Split arrive avec SEC-001 auth tiers.
- Pas d'auth applicative (JWT / tiers). `user_tier = "anonymous"` reste hardcodé phase MVP (cf vision.md).
- Pas de Workflow Terraform CI séparé (`terraform.yml` plan-on-PR) — déféré.
- Pas de scripts `down` migration. PITR Cloud SQL = filet de sécurité unique V1.
- Pas de re-indexation du corpus existant : la migration `BAAI/bge-m3` → `mistral-embed` repose sur l'identité de dimension (1024) et reste un swap config.
- Pas d'activation Cloud Armor (Cloudflare front suffit V1).
- Pas de domain mapping Cloud Run sur `workers` (workers reste `ingress=internal`, accédé via service-to-service auth IAM ou URL `*.run.app`).

## Pre-conditions

- Projet GCP existant avec billing activé, API Cloud Run / Cloud SQL / Secret Manager / Artifact Registry / IAM Credentials / Cloud Resource Manager / Cloud Billing Budget / Compute / Storage / Service Usage activées (Terraform peut les activer, ou pré-activation manuelle documentée).
- Domaine `nocilia.fr` (+ `.com` / `.org` / `.eu` / `.net`) déjà délégué aux nameservers Cloudflare (acte registrar humain, hors-ticket).
- Account Cloudflare API token disponible avec scope `Zone:Edit` + `DNS:Edit` + `Page Rules:Edit` + `Bot Management:Edit` sur les zones concernées. Token stocké en GitHub Actions secret (out-of-band) ou variable Terraform locale chiffrée — il n'entre PAS dans Secret Manager GCP (provider Cloudflare, pas Cloud Run runtime).
- Compte Mistral actif, clé API valide, cap budget console Mistral configuré (acte humain, hors-ticket — cf vision.md Q8).
- ADR-0003 amendé à `accepted (activated)` (déjà mergé `95d911f`).
- `docs/runbook/rollback.md` scaffold présent (déjà mergé `95d911f`) — INFRA-002 finalise son contenu si l'ébauche actuelle est partielle.
- Branche `main` protégée : seul le merge PR déclenche `deploy.yml` (settings GitHub humain).

## Failure modes

- **WIF mal-condition / fuite cross-repo** : un push depuis un fork ou une autre branche tente l'authentification GCP → token exchange refusé par STS GCP (condition CEL `repository` + `ref` non satisfaite) → step `auth` du workflow échoue avec `failed to generate Google Cloud access token`. Aucune ressource GCP altérée.
- **Smoke test KO post-deploy canary** : `curl /healthz` non-2xx dans la fenêtre de smoke → workflow exécute `gcloud run services update-traffic --to-revisions=PREVIOUS=100` sur gateway ET workers, log la révision fautive, `exit 1`. La révision défaillante reste taggée mais ne reçoit pas de trafic. Notification = échec du run GHA (email GitHub par défaut).
- **Cloud SQL Auth Proxy sidecar unreachable** : socket Unix indisponible côté workers → erreur `sqlx` / `asyncpg` `connection refused` au boot → healthz workers KO → smoke test KO → rollback automatique (cf ci-dessus).
- **Secret `MISTRAL_API_KEY` manquant ou révoqué** : workers boot mais retournent 5xx sur `/v1/generate` et `/v1/ingest` (échec auth Mistral) → Langfuse error rate spike → détection humaine via runbook. Pas de circuit breaker V1.
- **Budget €50 dépassé** : email Cloud Billing envoyé à l'owner. Aucun stop automatique du trafic V1 (cap dur = cap Mistral console, hors GCP). Action humaine requise (downscale ou désactivation manuelle).
- **Cloudflare rate-limit déclenché** : client dépassant 100 req/min/IP reçoit HTTP 429 (ou challenge) directement de Cloudflare. Aucune métrique applicative émise V1 (pas d'observabilité Cloudflare → GCP).
- **Embedder switch dim mismatch** : si `mistral-embed` retournait une dim ≠ 1024, le ingest workers lèverait `RuntimeError("expected embedding dim 1024, got <N>")` (assertion déjà présente `workers/src/archiviste_workers/embedder.py`). Boot reste OK, ingest échoue → détection en CI / dev avant promotion.
- **Cache Cloudflare DNS sur switch domaine** : propagation TTL initial → `archiviste.nocilia.fr` peut renvoyer NXDOMAIN < TTL. Vérification humaine post-deploy, pas de mitigation Terraform.

## Touch points (informatif, non contraignant pour l'architect)

- `infra/terraform/` — `main.tf`, `variables.tf`, `outputs.tf`, `versions.tf`, plus modules ou fichiers découpés par domaine (`cloud_run.tf`, `cloud_sql.tf`, `gcs.tf`, `secrets.tf`, `iam_wif.tf`, `cloudflare.tf`, `budget.tf`, `artifact_registry.tf`). Backend GCS dans `versions.tf` ou `backend.tf`.
- `.github/workflows/deploy.yml` — nouveau workflow WIF + canary + smoke + auto-rollback (cf AC-11/12).
- `workers/src/archiviste_workers/embedder.py` — swap implémentation `SentenceTransformer('BAAI/bge-m3')` → client Mistral embeddings via `LLM_API_KEY` / `MISTRAL_API_KEY`. Préserver l'assertion `EMBEDDING_DIM == 1024`.
- `workers/src/archiviste_workers/settings.py` — ajout / réutilisation `mistral_api_key: SecretStr`, `embedding_provider` ou réutilisation de `llm_provider`. Choix exact = architect.
- `workers/pyproject.toml` — ajout dépendance client Mistral si non déjà présente ; **suppression possible** de `sentence-transformers` + `torch` du runtime workers (gain image size pour Cloud Run scale-to-zero) si l'architect confirme aucun autre usage.
- `infra/docker/workers.Dockerfile` — réduction taille image si dépendances HF retirées (out-of-runtime).
- `docs/runbook/rollback.md` — finalisation (les 3 commandes + PITR DB).
- `docs/adr/0003-terraform-deferred.md` — déjà amendé `2026-05-18`, lien croisé éventuel.
- `CHANGELOG.md` — entrée `## [Unreleased]` section Infra.
- `scripts/check-ports.sh` — vérifier conformité (gateway 8080, workers 8000 inchangés en local).
- **Non touchés** : `migrations/*.sql` (zéro migration), `specs/openapi/gateway-to-workers.yml` (contrat REST inchangé), `gateway/` code applicatif (déploiement only, pas de logique métier).

## Test oracle

- AC-1 : contract · `terraform fmt -recursive -check infra/terraform/` exit 0 ET `terraform -chdir=infra/terraform validate` exit 0 (lancé en CI ou pré-commit local).
- AC-2 / AC-3 / AC-4 / AC-5 / AC-6 / AC-8 / AC-9 : intégration manuelle post-`terraform apply` · vérification via `gcloud run services describe`, `gcloud sql instances describe`, `gsutil ls -L gs://archiviste-conversations`, `gcloud secrets list`, `gcloud iam service-accounts get-iam-policy`, `gcloud billing budgets list`, plus introspection Cloudflare via API ou dashboard. Pas de test automatisé.
- AC-7 : contract · revue diff Terraform — assert présence exacte de la condition CEL `assertion.repository == 'doudoune444/archiviste-nocilia' && assertion.ref == 'refs/heads/main'` dans le binding `roles/iam.workloadIdentityUser`. Test négatif : tentative manuelle de push depuis branche `feat/*` → step `auth` échoue (vérifié 1 fois en dry-run avant merge).
- AC-10 : intégration · `cd workers && uv run pytest tests/test_embedder.py` passe avec le nouveau client Mistral mocké (HTTP mock). `tests/test_embedder_properties.py` (hypothesis) vérifie toujours `len(vector) == 1024`. Test live optionnel `pytest -m live` (skipped par défaut) appelle l'API Mistral réelle avec une clé de test.
- AC-11 : contract · `grep -E 'workload_identity_provider|service_account' .github/workflows/deploy.yml` matche ET `! grep -E 'credentials_json|GCP_SA_KEY' .github/workflows/deploy.yml` (zéro JSON key).
- AC-12 : intégration end-to-end · premier run de `deploy.yml` sur merge `main` : observation des étapes en ordre, smoke test pass, promote 100 %. Test négatif : 1 commit volontairement cassant (par ex. crash au boot gateway) sur une branche de test mergée temporairement → workflow doit déclencher rollback PREVIOUS=100 et exit 1. (À mener 1× post-merge, documenté dans le runbook, non rejoué en CI permanente.)
- AC-13 : revue manuelle · `grep -c 'gcloud run' docs/runbook/rollback.md >= 3` ET la section DB référence `gcloud sql backups`.
- AC-14 : intégration externe · `curl -sf https://archiviste.nocilia.fr/healthz` retourne 200 depuis un poste hors-GCP ; `curl -sI https://archiviste.nocilia.com/` retourne `301` `Location: https://archiviste.nocilia.fr/` (idem `.org`/`.eu`/`.net`).

## Performance / SLO

- Cold start Cloud Run gateway 256 MB : ≤ 5 s p95 observé (informatif, pas un gate ; mesure manuelle post-deploy).
- Cold start Cloud Run workers 512 MB sans modèle bundlé : ≤ 7 s p95 observé (gain attendu vs BGE-M3 in-container : embedder devient appel HTTP Mistral).
- p95 chat round-trip externe (Cloudflare → gateway → workers → Mistral → réponse) : pas de gate INFRA-002, l'objectif vision `< 3 s p95` reste OPS-001.
- Budget GCP : ≤ 50 EUR / mois (alert, pas un gate technique).

## Security / trust boundary

- **Zéro clé JSON de service account** persistée : auth GHA exclusivement via Workload Identity Federation, condition CEL stricte repo + branch `main`.
- IAM least-privilege approximatif V1 : 1 SA runtime partagé `archiviste-runtime@` (split = V2 SEC-001). `storage.objectAdmin` est bucket-scoped via `iam_binding` sur la ressource bucket, **jamais project-wide**.
- `MISTRAL_API_KEY` exclusivement en Secret Manager, injectée à `archiviste-workers` via `secret_key_ref`. Jamais en clair dans le code, le repo, ni les logs (cf `.claude/rules/secret-hygiene.md`).
- Cloud SQL : pas d'IP publique, accès uniquement via Cloud SQL Auth Proxy sidecar socket Unix (pas de VPC connector). Le SA runtime a `cloudsql.client`, pas `cloudsql.admin`.
- GCS bucket `archiviste-conversations` : uniform bucket-level access, `public_access_prevention = enforced`, lifecycle TTL 30j. Aucun ACL legacy.
- Cloudflare TLS Full Strict obligatoire : Cloudflare valide le cert origin Cloud Run. Pas de mode `flexible` (interdit).
- Bot Fight Mode ON + Security Level Medium + 1 rate-limit rule 100 req/min/IP = perimeter V1. Pas de sliding window app-level (SEC-002 V2).
- Pas de security headers (HSTS / CSP / etc.) dans ce ticket : SEC-003 immédiatement après.
- Aucune fonctionnalité fetch URL runtime ajoutée (cohérent threat-model W-E-1 vision.md).
- Secrets Cloudflare API token : hors Secret Manager GCP, stocké en GitHub Actions secret ou Terraform local var chiffrée. Document dans le runbook.

## Observability

- Budget alert Cloud Billing `archiviste-beta-monthly` 50 EUR → email owner (seule alerte V1).
- Logs Cloud Run par défaut accessibles via Cloud Logging console (pas de filtres / dashboards customs V1).
- Langfuse traces LLM : déjà en place côté workers (FOUND-002), continue de fonctionner via `LANGFUSE_*` env vars (hors-scope INFRA-002 sauf vérification que les env vars sont propagées par Terraform si elles existent en local).
- Pas d'uptime checks Cloud Monitoring, pas de log-based metrics, pas d'alert policies sur 5xx / latency. (OBS-001 V2.)
- Détection rollback = humaine : Cloudflare analytics + Langfuse error rate + report user `/healthz` (cf `docs/runbook/rollback.md` section Détection).
- Aucune métrique Cloudflare exportée vers GCP V1.

## Effort estimate

L — surface attendue : ~600-900 LOC HCL Terraform (Cloud Run × 2 + Cloud SQL + GCS + Secret Manager + IAM + WIF + Cloudflare + budget + Artifact Registry + backend state) + ~80-150 LOC GHA `deploy.yml` + ~40-80 LOC Python embedder swap + ajustements settings/Dockerfile workers + finalisation runbook. Dépasse le seuil vertical-slice `≤ 300 LOC` ; voir Open questions pour proposition de split.

## Decisions (auto-arbitrées vs vision.md, 2026-05-18)

- **D-1 — Split scope : 1 spec INFRA-002, 4 PRs successifs partageant la spec**. Vision §107/161 traite INFRA-002 comme 1 ticket unique. Vertical-slice rule respectée au niveau PR (plan architect découpe en 4 PRs : a=Terraform core, b=Cloudflare, c=GHA deploy.yml + runbook, d=embedder switch). Pas de spec filles, pas de démultiplication. Ordre PRs imposé : a → b → c → d. Aucun PR ne mergée sur `main` sans les précédents (sinon ship cassé).
- **D-2 — Provider Cloudflare Terraform** (`cloudflare/cloudflare`), token en GitHub Actions secret + Terraform var locale. Cohérent ADR-0003 (IaC source de vérité). Scripts manuels rejetés.
- **D-3 — Bootstrap WIF** : nouveau fichier `docs/runbook/bootstrap-gcp.md` (one-shot doc humain, hors Terraform). Pré-condition ajoutée plus haut. PR a inclut ce fichier.
- **D-4 — Custom domain mapping Cloud Run** : tenter `google_cloud_run_domain_mapping` PR b. Si preview non-GA dans europe-west9 au moment du `terraform apply` → fallback Cloudflare proxy direct vers `*.run.app` URL gateway, `Host` header rewrite côté Cloudflare. Pas de bloqueur. Choix tranché par architect au /plan.
- **D-5 — INSTANCE_CONNECTION_NAME** : env var injectée par Terraform sur les 2 services Cloud Run (`spec.template.metadata.annotations["run.googleapis.com/cloudsql-instances"]` + env `INSTANCE_CONNECTION_NAME`). Détail architect.
- **D-6 — Ré-ingestion corpus avec `mistral-embed`** : ticket séparé V2 `ING-016 re-index prod corpus`. Hors-scope INFRA-002. Justification : V1 beta peut ship avec corpus ingéré local BGE-M3 si dim 1024 strictement identique (validé par vision Q7). Cas warmup prod = ré-ingest manuel post-deploy via script existant `scripts/ingest.py`.
- **D-7 — Aucune `google_service_account_key` resource** dans le diff Terraform. Garde-fou : grep CI `! grep -rE 'google_service_account_key' infra/terraform/` (à ajouter dans le plan architect comme test contract).

## Status

ready
