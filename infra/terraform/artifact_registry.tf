resource "google_artifact_registry_repository" "archiviste" {
  repository_id = "archiviste"
  format        = "DOCKER"
  location      = var.region
  description   = "Docker images for archiviste-gateway and archiviste-workers."
  labels        = local.labels
}
