# Bootstrap GCP — INFRA-002 one-shot pre-conditions

One-shot operator procedure. Run once before the first `terraform apply`. Never re-run unless
rebuilding from scratch. All commands assume `gcloud` authenticated with Owner on the project.

## 1. Authenticate

```bash
gcloud auth login
gcloud config set project <PROJECT_ID>
```

## 2. Enable required GCP APIs

```bash
gcloud services enable \
  run.googleapis.com \
  sqladmin.googleapis.com \
  secretmanager.googleapis.com \
  artifactregistry.googleapis.com \
  iamcredentials.googleapis.com \
  cloudresourcemanager.googleapis.com \
  billingbudgets.googleapis.com \
  cloudbilling.googleapis.com \
  compute.googleapis.com \
  storage.googleapis.com \
  serviceusage.googleapis.com \
  iam.googleapis.com
```

## 3. Create Terraform state bucket

The `backend "gcs"` in `versions.tf` references `archiviste-tf-state`. Create it manually
(Terraform cannot bootstrap its own state bucket):

```bash
gsutil mb -l europe-west9 gs://archiviste-tf-state
gsutil versioning set on gs://archiviste-tf-state
```

## 4. Initialise and apply Terraform

Cloud Run fails on first `terraform apply` if images do not exist in Artifact Registry yet
(chicken-and-egg: Terraform creates the AR repo, GHA pushes real images, but GHA needs WIF
from Terraform). Follow this three-step sequence to break the cycle:

### 4a. Create the Artifact Registry repo only

```bash
cd infra/terraform
terraform init
terraform apply -target=google_artifact_registry_repository.archiviste \
  -var-file=terraform.tfvars
```

`terraform.tfvars` is gitignored. Minimal example (never commit):

```hcl
project_id            = "<PROJECT_ID>"
billing_account       = "<BILLING_ACCOUNT_ID>"
budget_email          = "owner@example.com"
cloudflare_account_id = "<CF_ACCOUNT_ID>"
cloudflare_api_token  = "<CF_API_TOKEN>"
```

### 4b. Push placeholder images

```bash
# Target: europe-west9-docker.pkg.dev/<PROJECT_ID>/archiviste/{gateway,workers}:latest
gcloud auth configure-docker europe-west9-docker.pkg.dev

docker pull gcr.io/google-containers/pause:3.9
docker tag gcr.io/google-containers/pause:3.9 \
  europe-west9-docker.pkg.dev/<PROJECT_ID>/archiviste/gateway:latest
docker push europe-west9-docker.pkg.dev/<PROJECT_ID>/archiviste/gateway:latest

docker tag gcr.io/google-containers/pause:3.9 \
  europe-west9-docker.pkg.dev/<PROJECT_ID>/archiviste/workers:latest
docker push europe-west9-docker.pkg.dev/<PROJECT_ID>/archiviste/workers:latest
```

### 4c. Full apply

```bash
terraform plan -var-file=terraform.tfvars   # review before apply
terraform apply -var-file=terraform.tfvars
```

## 5. Bootstrap MISTRAL_API_KEY secret version

Terraform creates the secret resource but NOT the version (version = operator secret, never
in code or state). After `terraform apply`:

```bash
echo -n "<YOUR_MISTRAL_API_KEY>" | \
  gcloud secrets versions add MISTRAL_API_KEY --data-file=-
```

This must happen BEFORE the first `deploy.yml` GHA run — workers boot fails without it.

## 6. Bootstrap pgvector extension

Cloud SQL `db-f1-micro` Postgres 16. First set the `postgres` superuser password (Cloud SQL
auto-generates one; retrieve it from the GCP Console or set it explicitly):

```bash
gcloud sql users set-password postgres \
  --instance=archiviste-db \
  --password=<POSTGRES_SUPERUSER_PASSWORD>
```

Then connect and activate the extension:

```bash
gcloud sql connect archiviste-db --user=postgres --database=archiviste
```

Inside psql:

```sql
CREATE EXTENSION IF NOT EXISTS vector;
```

## 7. Cloudflare API token

Scope required: `Zone:Edit` + `DNS:Edit` + `Page Rules:Edit` + `Bot Management:Edit` on
zones `nocilia.fr`, `nocilia.com`, `nocilia.org`, `nocilia.eu`, `nocilia.net`.

Token is stored **outside Secret Manager GCP** (it's a Cloudflare provider credential, not a
Cloud Run runtime secret). Store it as:

- GitHub Actions secret `CLOUDFLARE_API_TOKEN` (for future CI Terraform plan — V2 only).
- Local `terraform.tfvars` (gitignored) for manual `terraform apply` V1.

## 8. Verify IAM database authentication

Cloud Run v2 uses the integrated Cloud SQL Auth Proxy (declared via `volumes.cloud_sql_instance`
+ annotation `run.googleapis.com/cloudsql-instances`). Verify that the proxy supports IAM
authentication for the runtime SA before the first `deploy.yml` run:

```bash
# Connect as the IAM SA user (requires roles/cloudsql.instanceUser granted by Terraform)
gcloud sql connect archiviste-db \
  --user=archiviste-runtime@<PROJECT_ID>.iam \
  --database=archiviste
```

If this fails with `pg_authentication_failed`, the integrated proxy may not support
`--auto-iam-authn` for this Cloud Run v2 configuration. In that case, raise a blocker — see
`docs/blockers.md`. Do NOT proceed with `deploy.yml` until the connection succeeds.

## 9. Verify apply

```bash
gcloud run services describe archiviste-gateway --region=europe-west9
gcloud run services describe archiviste-workers --region=europe-west9
gcloud sql instances describe archiviste-db
gsutil ls -L gs://archiviste-conversations
gcloud secrets list
gcloud iam service-accounts list
gcloud billing budgets list --billing-account=<BILLING_ACCOUNT_ID>
```

## 10. Post-apply: add GITHUB_ACTIONS_SA_WIF to GitHub Actions secrets

Required by `deploy.yml`:

```bash
# Get WIF provider full name (from Terraform output)
terraform output wif_provider
terraform output gha_deploy_sa_email
```

Add as GitHub Actions secrets:
- `GCP_WIF_PROVIDER` = value of `wif_provider` output
- `GCP_SA_EMAIL` = value of `gha_deploy_sa_email` output
- `GCP_PROJECT_ID` = `<PROJECT_ID>`

## 11. Toggle Cloudflare Bot Fight Mode (one-shot manual UI)

Cloudflare provider 4.52+ removed `bot_fight_mode` from `cloudflare_zone_settings_override`
(now on paid plans only via `cloudflare_bot_management`). On Free plan, enable it once
manually:

1. Dashboard CF → zone `nocilia.fr` → **Security** → **Bots**
2. Toggle **Bot Fight Mode** to ON
3. Verify on `archiviste.nocilia.fr` after deploy — challenge page should appear for
   obvious bot traffic.

This is a single per-zone one-shot toggle, not a recurring action. Tracked in
`docs/blockers.md` 2026-05-27 INFRA-002 entry.

## 12. Configure Cloudflare rate-limit rule (one-shot manual UI)

`cloudflare_rate_limit` Terraform resource is deprecated 11+ months past EOL
(June 2025). Modern `cloudflare_ruleset` http_ratelimit migration deferred to V2
SEC-002 (which adds app-level tower_governor + Redis, making CF perimeter rule
redundant). For V1, configure manually once:

1. Dashboard CF → zone `nocilia.fr` → **Security** → **WAF** → **Rate limiting rules**
2. **Create rule**:
   - Rule name: `100 req/min/IP archiviste`
   - If incoming requests match: **Field** `Hostname` **Operator** `equals` **Value** `archiviste.nocilia.fr`
   - When rate exceeds: `100 requests` per `1 minute`
   - Then: **Block** (or **Managed Challenge** for softer UX)
   - Duration: `5 minutes`
3. Deploy.

Free plan allows 1 rate-limit rule per zone. AC-8 spec amended — see
`docs/blockers.md` 2026-05-27 INFRA-002 entry.
