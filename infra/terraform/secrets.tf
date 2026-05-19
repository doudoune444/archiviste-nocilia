# AC-5: single shared secret for Mistral API key (LLM + embeddings).
# The secret version is bootstrapped manually post-apply (see docs/runbook/bootstrap-gcp.md).
resource "google_secret_manager_secret" "mistral_api_key" {
  secret_id = "MISTRAL_API_KEY"
  labels    = local.labels

  replication {
    auto {}
  }
}
