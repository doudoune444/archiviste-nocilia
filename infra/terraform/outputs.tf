output "gateway_url" {
  description = "Cloud Run gateway service URL."
  value       = google_cloud_run_v2_service.gateway.uri
}

output "workers_url" {
  description = "Cloud Run workers service URL (internal ingress)."
  value       = google_cloud_run_v2_service.workers.uri
}

output "instance_connection_name" {
  description = "Cloud SQL instance connection name."
  value       = google_sql_database_instance.archiviste_db.connection_name
}

output "wif_provider" {
  description = "Full resource name of the WIF OIDC provider (for GHA google-github-actions/auth@v2)."
  value       = google_iam_workload_identity_pool_provider.github_oidc.name
}

output "gha_deploy_sa_email" {
  description = "Email of the GHA deploy service account."
  value       = google_service_account.gha_deploy.email
}

output "artifact_registry_repo" {
  description = "Artifact Registry repository URI base."
  value       = "${var.region}-docker.pkg.dev/${var.project_id}/${google_artifact_registry_repository.archiviste.repository_id}"
}
