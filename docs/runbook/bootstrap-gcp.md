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
project_id      = "<PROJECT_ID>"
billing_account = "<BILLING_ACCOUNT_ID>"
budget_email    = "owner@example.com"
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

## 7. Verify apply

```bash
gcloud run services describe archiviste-gateway --region=europe-west9
gcloud run services describe archiviste-workers --region=europe-west9
gcloud sql instances describe archiviste-db
gsutil ls -L gs://archiviste-conversations
gcloud secrets list
gcloud iam service-accounts list
gcloud billing budgets list --billing-account=<BILLING_ACCOUNT_ID>
```

## 8. Post-apply: add GITHUB_ACTIONS_SA_WIF to GitHub Actions secrets

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
