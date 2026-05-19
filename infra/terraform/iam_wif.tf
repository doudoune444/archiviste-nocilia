# AC-7: Workload Identity Federation for GitHub Actions OIDC.
# No SA JSON key resource is ever created — WIF only (D-7).

resource "google_iam_workload_identity_pool" "github" {
  workload_identity_pool_id = "github-pool"
  display_name              = "GitHub Actions pool"
  description               = "WIF pool for GHA deploy workflow."
}

resource "google_iam_workload_identity_pool_provider" "github_oidc" {
  workload_identity_pool_id          = google_iam_workload_identity_pool.github.workload_identity_pool_id
  workload_identity_pool_provider_id = "github-oidc"
  display_name                       = "GitHub OIDC provider"

  oidc {
    issuer_uri = "https://token.actions.githubusercontent.com"
  }

  attribute_mapping = {
    "google.subject"       = "assertion.sub"
    "attribute.actor"      = "assertion.actor"
    "attribute.repository" = "assertion.repository"
    "attribute.ref"        = "assertion.ref"
  }

  # AC-7: CEL condition — only main branch of the canonical repo.
  attribute_condition = "assertion.repository == 'doudoune444/archiviste-nocilia' && assertion.ref == 'refs/heads/main'"
}

resource "google_service_account_iam_member" "gha_wif_binding" {
  service_account_id = google_service_account.gha_deploy.name
  role               = "roles/iam.workloadIdentityUser"
  member             = "principalSet://iam.googleapis.com/${google_iam_workload_identity_pool.github.name}/attribute.repository/${var.github_repo}"
}
