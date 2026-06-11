# OBS-009: Cloud Run Job for one-shot live eval against prod workers.
# Executes manually via `gcloud run jobs execute archiviste-eval --region=<region>`.
# No Cloud Scheduler, no GHA trigger (AC-7 non-goal).
# Mirrors cloud_run_job.tf (archiviste-ingest) in structure: same SA, volume, DATABASE_URL
# IAM-auth pattern, max_retries=0, lifecycle ignore_changes on image.

resource "google_cloud_run_v2_job" "archiviste_eval" {
  name     = "archiviste-eval"
  location = var.region

  # eval:latest is retag'd by deploy.yml on each merge to main (same as workers:latest).
  # ignore_changes guards against out-of-band digest pins so plan stays clean.
  lifecycle {
    ignore_changes = [template[0].template[0].containers[0].image]
  }

  template {
    template {
      service_account = google_service_account.archiviste_runtime.email

      # max_retries=0: a failed eval run must not be silently retried — the operator
      # must inspect logs and re-execute manually (AC-1). Mirrors ingest Job.
      max_retries = 0

      # default task timeout is 600s; a throttled live Ragas run (RAGAS_MAX_WORKERS=1)
      # over the full golden set completes the judge phase in ~10-15 min. 7200s is
      # generous safety headroom against a slow Mistral tier / extra 429 backoff
      # (EVAL-008, widened EVAL-009).
      timeout = "7200s"

      # Cloud SQL managed volume: same pattern as cloud_run_job.tf (ingest Job).
      # The Cloud Run integrated Auth Proxy exposes the Unix socket at /cloudsql;
      # DATABASE_URL routes psycopg2 to it via the ?host= query param (AC-6).
      volumes {
        name = "cloudsql"
        cloud_sql_instance {
          instances = [google_sql_database_instance.archiviste_db.connection_name]
        }
      }

      containers {
        name  = "eval"
        image = "${local.ar_base}/eval:latest"

        # OBS-009 two-step command (AC-7, AC-5):
        #   1. Fetch golden set via eval.fetch_golden (google-cloud-storage, 30 s timeout).
        #      Writes to /tmp/golden_qa.jsonl (writable tmpfs in the container).
        #   2. Run the Ragas eval runner in live+persist mode against prod workers.
        # Workers URL is the Cloud Run service URI; OIDC auth (OBS-007) is auto-triggered
        # because the URL is https + non-loopback (is_authenticated_target returns True).
        # archiviste-runtime already holds roles/run.invoker on workers (cloud_run.tf:287).
        command = [
          "/bin/bash", "-c",
          "python -m eval.fetch_golden --bucket archiviste-lore-corpus --object golden/golden_qa.jsonl --dest /tmp/golden_qa.jsonl && python -m eval.ragas_runner --mode live --persist --set /tmp/golden_qa.jsonl --output /tmp/run.json --workers-url ${google_cloud_run_v2_service.workers.uri}",
        ]

        resources {
          # Cloud SQL volume forces CPU always-allocated — pin cpu so apply does not
          # drop it from limits and fight the always-allocated requirement (mirrors ingest Job).
          limits = {
            cpu    = "1000m"
            memory = "1Gi"
          }
        }

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
          # Scheme postgresql:// (plain libpq/psycopg2). eval/persist.py uses psycopg2
          # which cannot parse the postgresql+asyncpg:// SQLAlchemy dialect scheme.
          # SA username = email with ".gserviceaccount.com" stripped (Cloud SQL IAM user
          # convention, cloud_sql.tf). IAM-auth socket mechanism via host= is unchanged.
          value = "postgresql://${replace(trimsuffix(google_service_account.archiviste_runtime.email, ".gserviceaccount.com"), "@", "%40")}@/archiviste?host=/cloudsql/${google_sql_database_instance.archiviste_db.connection_name}"
        }

        # RAGAS_JUDGE_PROVIDER=mistral: metrics.py defaults to mistral but we pin it
        # explicitly so the job is self-documenting and resilient to default changes.
        env {
          name  = "RAGAS_JUDGE_PROVIDER"
          value = "mistral"
        }

        # RAGAS_MAX_WORKERS: Ragas judge concurrency, tuned to the Mistral tier's rate
        # limit. 1 avoids 429 storms that blow the task timeout (EVAL-009). Tunable live
        # via `gcloud run jobs update --update-env-vars` without an image rebuild.
        env {
          name  = "RAGAS_MAX_WORKERS"
          value = "1"
        }

        # LLM_API_KEY: read by eval/metrics.py build_ragas_judge() (line 111).
        # Secret Manager reference — never a plaintext value (AC-6, security.md §A02).
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
}
