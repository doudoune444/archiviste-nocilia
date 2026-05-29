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

Cloud Run requires the first revision to pass the HTTP startup probe on `PORT=8080`.
A no-op container (e.g. `pause`) cannot satisfy this — use Google's official Cloud Run
hello sample which listens on `$PORT` by default. The image was also relocated from
`gcr.io/google-containers/*` (deprecated) to `registry.k8s.io/*` for pause and
`us-docker.pkg.dev/cloudrun/container/hello` for the Cloud Run sample.

```bash
# Target: europe-west9-docker.pkg.dev/<PROJECT_ID>/archiviste/{gateway,workers}:latest
gcloud auth configure-docker europe-west9-docker.pkg.dev

docker pull us-docker.pkg.dev/cloudrun/container/hello
docker tag us-docker.pkg.dev/cloudrun/container/hello \
  europe-west9-docker.pkg.dev/<PROJECT_ID>/archiviste/gateway:latest
docker push europe-west9-docker.pkg.dev/<PROJECT_ID>/archiviste/gateway:latest

docker tag us-docker.pkg.dev/cloudrun/container/hello \
  europe-west9-docker.pkg.dev/<PROJECT_ID>/archiviste/workers:latest
docker push europe-west9-docker.pkg.dev/<PROJECT_ID>/archiviste/workers:latest
```

If a previous bootstrap attempt deployed a broken placeholder (e.g. `pause`), the
existing Cloud Run revision is pinned to that digest. Force a new revision after
pushing the working image:

```bash
gcloud run services update archiviste-workers \
  --image=europe-west9-docker.pkg.dev/<PROJECT_ID>/archiviste/workers:latest \
  --region=europe-west9
gcloud run services update archiviste-gateway \
  --image=europe-west9-docker.pkg.dev/<PROJECT_ID>/archiviste/gateway:latest \
  --region=europe-west9
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

## 5b. Bootstrap JWT Ed25519 keypair + GCS signing placeholder

The gateway refuses to boot without 4 env vars (`gateway/src/config.rs:58-87`).
Terraform provisions the *plumbing* (2 Secret Manager secrets for the JWT keys +
an inline placeholder for the unused GCS signing field). The operator generates
the keypair and pushes both versions one-shot below.

### Generate the Ed25519 JWT keypair

```bash
openssl genpkey -algorithm ED25519 -out /tmp/jwt-private.pem
openssl pkey -in /tmp/jwt-private.pem -pubout -out /tmp/jwt-public.pem
```

### Push both keys to Secret Manager

```bash
gcloud secrets versions add JWT_ED25519_PRIVATE_KEY_PEM \
  --data-file=/tmp/jwt-private.pem
gcloud secrets versions add JWT_ED25519_PUBLIC_KEY_PEM \
  --data-file=/tmp/jwt-public.pem
```

Rotation: re-run the two `versions add` calls only. No `terraform apply` needed;
the next Cloud Run revision picks up `version = "latest"` automatically.

### Clean up local key material

```bash
shred -u /tmp/jwt-private.pem /tmp/jwt-public.pem
```

### GCS signing — no key required

GCS V4 signed URLs are now generated via IAM `signBlob` auto-impersonation
(SEC-004). The gateway calls `iamcredentials.googleapis.com` using a bearer
token obtained from the Cloud Run metadata server. No SA private key is needed
and `GCS_SIGNING_PRIVATE_KEY_PEM` no longer exists in the configuration.

See `docs/runbook.md §5` for the full post-deploy verification procedure and
the local-dev `gcloud` commands.

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
