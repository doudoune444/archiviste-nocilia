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
    }
  }

  deletion_protection = true
}

resource "google_sql_database" "archiviste" {
  name     = "archiviste"
  instance = google_sql_database_instance.archiviste_db.name
}
