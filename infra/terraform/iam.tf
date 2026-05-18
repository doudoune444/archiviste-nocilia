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
locals {
  runtime_project_roles = [
    "roles/cloudsql.client",
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
