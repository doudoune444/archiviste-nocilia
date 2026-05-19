# AC-3: Cloud SQL Postgres 16 instance with pgvector support.
# pgvector is activated via bootstrap SQL post-create (see docs/runbook/bootstrap-gcp.md).
resource "google_sql_database_instance" "archiviste_db" {
  name             = "archiviste-db"
  database_version = "POSTGRES_16"
  region           = var.region

  settings {
    tier = "db-f1-micro"

    disk_size = 10
    disk_type = "PD_SSD"

    backup_configuration {
      enabled                        = true
      point_in_time_recovery_enabled = true
      transaction_log_retention_days = 7
      backup_retention_settings {
        retained_backups = 7
      }
    }

    ip_configuration {
      # No public IP — Cloud SQL Auth Proxy sidecar via Unix socket only.
      ipv4_enabled = false
      # MED-4: defense-in-depth — reject unencrypted connections even though proxy handles TLS.
      ssl_mode = "ENCRYPTED_ONLY"
    }
  }

  deletion_protection = true
}

resource "google_sql_database" "archiviste" {
  name     = "archiviste"
  instance = google_sql_database_instance.archiviste_db.name
}

# HIGH-4: DATABASE_URL references user "archiviste" — must exist or connections fail with
# "FATAL: role archiviste does not exist". IAM auth type = no password to manage,
# cohérent avec Cloud SQL Auth Proxy sidecar (proxy handles TLS + IAM token exchange).
# Cloud SQL IAM SA user name must be the full SA email.
resource "google_sql_user" "archiviste_runtime" {
  instance = google_sql_database_instance.archiviste_db.name
  name     = google_service_account.archiviste_runtime.email
  type     = "CLOUD_IAM_SERVICE_ACCOUNT"
}
