# Bootstrap GCP — INFRA-002 one-shot pre-conditions

> **PR merge order (plan D-1): PR-a must be merged before PR-b.**
> `cloudflare.tf` references `google_cloud_run_v2_service.gateway` (defined in PR-a `cloud_run.tf`).
> `terraform validate` on PR-b standalone against `origin/main` will fail until PR-a is merged.
> Merge sequence: **PR-a (Terraform core GCP) → PR-b (Cloudflare) → PR-c (GHA deploy) → PR-d (embedder)**.

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

```bash
cd infra/terraform
terraform init
terraform plan -var-file=terraform.tfvars   # review before apply
terraform apply -var-file=terraform.tfvars
```

`terraform.tfvars` is gitignored. Minimal example (never commit):

```hcl
project_id            = "<PROJECT_ID>"
billing_account       = "<BILLING_ACCOUNT_ID>"
budget_email          = "owner@example.com"
cloudflare_account_id = "<CF_ACCOUNT_ID>"
cloudflare_api_token  = "<CF_API_TOKEN>"
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

Cloud SQL `db-f1-micro` Postgres 16. After `terraform apply`:

```bash
gcloud sql connect archiviste-db --user=postgres --database=archiviste
```

Then inside psql:

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

## 8. Verify apply

```bash
gcloud run services describe archiviste-gateway --region=europe-west9
gcloud run services describe archiviste-workers --region=europe-west9
gcloud sql instances describe archiviste-db
gsutil ls -L gs://archiviste-conversations
gcloud secrets list
gcloud iam service-accounts list
gcloud billing budgets list --billing-account=<BILLING_ACCOUNT_ID>
```

## 9. Post-apply: add GITHUB_ACTIONS_SA_WIF to GitHub Actions secrets

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
