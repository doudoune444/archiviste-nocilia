# OPS-005: Cloud Run Job for lore/ ingestion triggered by GHA on push main paths:['lore/**'].
# Separated from cloud_run.tf (services) because a Job has a distinct lifecycle: no traffic
# management, no scaling, no ingress — a dedicated file makes the diff and the intent clearer.

resource "google_cloud_run_v2_job" "archiviste_ingest" {
  name     = "archiviste-ingest"
  location = var.region

  # Image pinned to workers:latest, which deploy.yml retags to current main on every push
  # (deploy.yml "build and push workers"). The Job pulls the moved tag on its next execution;
  # the terraform string never changes. ignore_changes guards against any out-of-band digest
  # pin so plan stays clean.
  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image]
  }

  template {
    template {
      service_account = google_service_account.archiviste_runtime.email

      # max_retries = 0: ING-001 exit 1 signals ≥1 file error (AC-7). A retry would re-run the
      # full scan and potentially mask the failure signal; the GHA step must see the exit-1 →
      # Failed mapping immediately. Exit 2 (init fatal) should also not be retried.
      max_retries = 0

      # Cloud SQL managed volume: same pattern as the workers service (cloud_run.tf:155-159).
      # The integrated Cloud SQL Auth Proxy is injected by Cloud Run when this annotation +
      # volume are present — no separate sidecar container is needed (R1: AC wording
      # "sidecar" interpreted as IAM-authn connection, not a literal proxy container).
      # The volume is mounted at /cloudsql and the DATABASE_URL ?host= query param routes
      # the asyncpg driver to the Unix-domain socket exposed there.
      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.archiviste_db.connection_name]
        }
      }

      containers {
        name  = "ingest"
        image = "${local.ar_base}/workers:latest"

        # Defect A fix: run the uv venv interpreter, not the system `python`. The package is
        # installed in /app/.venv (the workers service activates it via `uv run`); bare `python`
        # raises ModuleNotFoundError: No module named 'archiviste_workers'.
        #
        # NOT YET FUNCTIONAL — corpus channel deferred to OPS-006. The ingest CLI requires a
        # `.git/` root (find_repo_root, cli.py:58-73) AND the lore/ corpus physically present
        # under that root; the workers image ships only src/, never the corpus (kept out of the
        # public repo by .gitignore `/lore/*`). OPS-006 wires the private channel: a `git init`
        # ephemeral root + `gcloud storage rsync gs://archiviste-lore-corpus lore/` before this
        # command. Until then this Job exits 2 (find_repo_root: no .git/ above /app) — expected.
        command = ["/app/.venv/bin/python", "-m", "archiviste_workers.ingest", "--path", "lore/"]

        resources {
          # Cloud SQL volume forces CPU always-allocated — pin cpu so apply does not drop it
          # from limits and fight the always-allocated requirement (same constraint as services).
          limits = {
            cpu    = "1000m"
            memory = "512Mi"
          }
        }

        # AC-2: Cloud SQL IAM authn — same env set as workers service (cloud_run.tf:183-221).
        env {
          name  = "INSTANCE_CONNECTION_NAME"
          value = google_sql_database_instance.archiviste_db.connection_name
        }

        env {
          name  = "CLOUD_SQL_IAM_AUTH"
          value = "true"
        }

        env {
          name = "DATABASE_URL"
          # HIGH-5: SA email contains '@' — percent-encode as '%40' (RFC 3986 userinfo).
          # Python asyncpg driver; scheme postgresql+asyncpg. SA username = email with
          # ".gserviceaccount.com" stripped (Cloud SQL IAM user convention, cloud_sql.tf:61).
          value = "postgresql+asyncpg://${replace(trimsuffix(google_service_account.archiviste_runtime.email, ".gserviceaccount.com"), "@", "%40")}@/archiviste?host=/cloudsql/${google_sql_database_instance.archiviste_db.connection_name}"
        }

        # AC-1/AC-2: MISTRAL_API_KEY via Secret Manager secret_key_ref — never in plaintext.
        # The ingest embedder uses mistral-embed via API (embedder.py DEFAULT_MODEL_NAME);
        # no local model download (~100 MB tokenizer only, not the 2.3 GiB BAAI/bge-m3).
        env {
          name = "MISTRAL_API_KEY"
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
}
