# AC-2 + AC-3 + AC-5: Cloud Run services with Cloud SQL Auth Proxy sidecar.
# Images use :latest as placeholder; GHA deploy.yml overrides with :<git_sha>.

locals {
  ar_base = "${var.region}-docker.pkg.dev/${var.project_id}/archiviste"
}

resource "google_cloud_run_v2_service" "gateway" {
  name     = "archiviste-gateway"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_ALL"

  # MED-5: GHA overrides image with :<git_sha> on each deploy — ignore drift to avoid
  # permanent terraform plan noise after every GHA run.
  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  template {
    service_account = google_service_account.archiviste_runtime.email

    scaling {
      min_instance_count = 0
    }

    annotations = {
      "run.googleapis.com/cloudsql-instances" = google_sql_database_instance.archiviste_db.connection_name
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.archiviste_db.connection_name]
      }
    }

    containers {
      name  = "gateway"
      image = "${local.ar_base}/gateway:latest"

      resources {
        limits = {
          memory = "256Mi"
        }
      }

      env {
        name  = "INSTANCE_CONNECTION_NAME"
        value = google_sql_database_instance.archiviste_db.connection_name
      }

      env {
        name  = "GCS_BUCKET"
        value = google_storage_bucket.conversations.name
      }

      env {
        name  = "DATABASE_URL"
        value = "postgresql+asyncpg://${google_service_account.archiviste_runtime.email}@/archiviste?host=/cloudsql/${google_sql_database_instance.archiviste_db.connection_name}"
      }

      # HIGH-1: gateway requires WORKERS_URL at boot (gateway/src/config.rs:40).
      # Populated from workers Cloud Run URI to avoid hardcoding.
      env {
        name  = "WORKERS_URL"
        value = google_cloud_run_v2_service.workers.uri
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
    }
  }
}

resource "google_cloud_run_v2_service" "workers" {
  name     = "archiviste-workers"
  location = var.region
  ingress  = "INGRESS_TRAFFIC_INTERNAL_ONLY"

  # MED-5: same image drift prevention as gateway.
  lifecycle {
    ignore_changes = [template[0].containers[0].image]
  }

  template {
    service_account = google_service_account.archiviste_runtime.email

    scaling {
      min_instance_count = 0
    }

    annotations = {
      "run.googleapis.com/cloudsql-instances" = google_sql_database_instance.archiviste_db.connection_name
    }

    volumes {
      name = "cloudsql"
      cloud_sql_instance {
        instances = [google_sql_database_instance.archiviste_db.connection_name]
      }
    }

    containers {
      name  = "workers"
      image = "${local.ar_base}/workers:latest"

      resources {
        limits = {
          memory = "512Mi"
        }
      }

      env {
        name  = "INSTANCE_CONNECTION_NAME"
        value = google_sql_database_instance.archiviste_db.connection_name
      }

      env {
        name  = "GCS_BUCKET"
        value = google_storage_bucket.conversations.name
      }

      env {
        name  = "DATABASE_URL"
        value = "postgresql+asyncpg://${google_service_account.archiviste_runtime.email}@/archiviste?host=/cloudsql/${google_sql_database_instance.archiviste_db.connection_name}"
      }

      # AC-5: MISTRAL_API_KEY injected via Secret Manager secret_key_ref.
      env {
        name = "LLM_API_KEY"
        value_source {
          secret_key_ref {
            secret  = google_secret_manager_secret.mistral_api_key.secret_id
            version = "latest"
          }
        }
      }

      volume_mounts {
        name       = "cloudsql"
        mount_path = "/cloudsql"
      }
    }
  }
}

# HIGH-3: gateway ingress=all requires explicit allUsers run.invoker binding for public access.
# Without this, Cloud Run v2 returns 403 to all unauthenticated requests (AC-14).
resource "google_cloud_run_v2_service_iam_member" "gateway_public_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.gateway.name
  role     = "roles/run.invoker"
  member   = "allUsers"
}

# HIGH-2: workers ingress=internal — gateway runtime SA needs run.invoker to call workers.
# Without this binding, service-to-service calls from gateway are rejected with 403.
resource "google_cloud_run_v2_service_iam_member" "workers_runtime_invoker" {
  project  = var.project_id
  location = var.region
  name     = google_cloud_run_v2_service.workers.name
  role     = "roles/run.invoker"
  member   = "serviceAccount:${google_service_account.archiviste_runtime.email}"
}
