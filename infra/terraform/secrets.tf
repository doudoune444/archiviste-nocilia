# AC-5: single shared secret for Mistral API key (LLM + embeddings).
# The secret version is bootstrapped manually post-apply (see docs/runbook/bootstrap-gcp.md).
resource "google_secret_manager_secret" "mistral_api_key" {
  secret_id = "MISTRAL_API_KEY"
  labels    = local.labels

  replication {
    auto {}
  }
}

# PR-e: JWT signing private key for archiviste-gateway (Ed25519).
# Version bootstrapped post-apply by operator (see docs/runbook/bootstrap-gcp.md §5b).
resource "google_secret_manager_secret" "jwt_ed25519_private_key" {
  secret_id = "JWT_ED25519_PRIVATE_KEY_PEM" # gitleaks:allow — Secret Manager resource ID, not a secret value
  labels    = local.labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "jwt_ed25519_private_key_accessor" {
  secret_id = google_secret_manager_secret.jwt_ed25519_private_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.archiviste_runtime.email}"
}

# PR-e: JWT verification public key (Ed25519). Routed through Secret Manager
# (mirror of private-key pattern) so rotation = `gcloud secrets versions add` only,
# no Terraform reapply, no operator paste into HCL. Public key is not secret material
# but the storage path is operationally symmetric and cheaper than maintaining a
# separate variable plumbing.
resource "google_secret_manager_secret" "jwt_ed25519_public_key" {
  secret_id = "JWT_ED25519_PUBLIC_KEY_PEM" # gitleaks:allow — Secret Manager resource ID, not a secret value
  labels    = local.labels

  replication {
    auto {}
  }
}

resource "google_secret_manager_secret_iam_member" "jwt_ed25519_public_key_accessor" {
  secret_id = google_secret_manager_secret.jwt_ed25519_public_key.secret_id
  role      = "roles/secretmanager.secretAccessor"
  member    = "serviceAccount:${google_service_account.archiviste_runtime.email}"
}
