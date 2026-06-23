# AC-6: two service accounts with least-privilege IAM bindings.

resource "google_service_account" "gha_deploy" {
  account_id   = "gha-deploy"
  display_name = "GitHub Actions deploy SA"
  description  = "Used by GHA deploy.yml via WIF. No JSON key ever generated."
}

resource "google_service_account" "archiviste_runtime" {
  account_id   = "archiviste-runtime"
  display_name = "Archiviste runtime SA (gateway + workers V1)"
  description  = "Runtime identity for both Cloud Run services (split = V2 SEC-001)."
}

# --- gha-deploy roles (project-wide) ---
locals {
  gha_deploy_roles = [
    "roles/run.admin",
    "roles/artifactregistry.writer",
    "roles/cloudsql.client",
    "roles/secretmanager.secretAccessor",
    "roles/iam.serviceAccountUser",
  ]
}

resource "google_project_iam_member" "gha_deploy" {
  for_each = toset(local.gha_deploy_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.gha_deploy.email}"
}

# --- archiviste-runtime roles (project-wide, excluding storage) ---
# roles/cloudsql.instanceUser is required for IAM DB authentication (CLOUD_IAM_SERVICE_ACCOUNT
# user type). Without it, the Cloud SQL Auth Proxy rejects the token exchange even when
# roles/cloudsql.client (network access) is present.
# Ref: https://cloud.google.com/sql/docs/postgres/add-manage-iam-users#grant-db-instance-user
locals {
  runtime_project_roles = [
    "roles/cloudsql.client",
    "roles/cloudsql.instanceUser",
    "roles/secretmanager.secretAccessor",
  ]
}

resource "google_project_iam_member" "runtime" {
  for_each = toset(local.runtime_project_roles)

  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.archiviste_runtime.email}"
}

# --- archiviste-runtime storage.objectAdmin bucket-scoped only (AC-6) ---
resource "google_storage_bucket_iam_member" "runtime_storage" {
  bucket = google_storage_bucket.conversations.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.archiviste_runtime.email}"
}

# SEC-004 AC-7: self-impersonation for IAM signBlob (GCS V4 signing).
# archiviste-runtime SA needs serviceAccountTokenCreator on itself so
# iamcredentials.signBlob accepts its own access token.
resource "google_service_account_iam_member" "runtime_token_creator_self" {
  service_account_id = google_service_account.archiviste_runtime.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.archiviste_runtime.email}"
}

# PLATFORM-004: gha-deploy needs run.invoker on the gateway so the canary smoke
# step can attach an identity token (audience=canary URL) without restoring
# allUsers invoker.  The deploy SA is bound here; the runtime SA binding lives in
# cloud_run.tf (gateway_runtime_invoker).
resource "google_cloud_run_v2_service_iam_member" "gateway_deploy_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.gateway.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.gha_deploy.email}"
}

# #253: the gateway runtime SA reads the workers Cloud Run Admin v2 descriptor
# (Ready condition) out-of-band to derive Workers' three-state health without
# waking the scale-to-zero service. Least-privilege: roles/run.viewer scoped to
# the workers service only, not project-wide.
resource "google_cloud_run_v2_service_iam_member" "runtime_workers_viewer" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.workers.name
  role     = "roles/run.viewer"
  member   = "serviceAccount:${google_service_account.archiviste_runtime.email}"
}

# PLATFORM-004: the canary smoke step mints an ID token via
# `print-identity-token --impersonate-service-account=gha-deploy`. Under WIF the
# active credential already impersonates gha-deploy, so the generateIdToken call
# is gha-deploy impersonating itself — that requires serviceAccountTokenCreator on
# itself (workloadIdentityUser alone does not cover the layered impersonation).
resource "google_service_account_iam_member" "gha_deploy_token_creator_self" {
  service_account_id = google_service_account.gha_deploy.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.gha_deploy.email}"
}

# OPS-003: CI migrate step impersonates runtime SA which owns the schema.
# gha-deploy needs serviceAccountTokenCreator on runtime SA to obtain short-lived
# credentials for cloud-sql-proxy --impersonate-service-account --auto-iam-authn.
resource "google_service_account_iam_member" "gha_deploy_token_creator_on_runtime" {
  service_account_id = google_service_account.archiviste_runtime.name
  role               = "roles/iam.serviceAccountTokenCreator"
  member             = "serviceAccount:${google_service_account.gha_deploy.email}"
}
