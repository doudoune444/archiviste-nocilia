# OPS-006 AC-1: private GCS bucket for the lore corpus (gdrive-sync → bucket → ingest).
# Uniform bucket-level access + enforced public_access_prevention: no ACL ever public.
# Kept in a dedicated file so the OPS-006 slice diff is self-contained (distinct from
# gcs.tf which holds the conversations bucket and iam.tf which holds project-wide bindings).
resource "google_storage_bucket" "lore_corpus" {
  name                        = "archiviste-lore-corpus"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  labels = local.labels
}

# AC-2: gha-deploy needs objectAdmin to rsync .md from gdrive-sync → bucket.
# Scoped to this bucket only — no project-wide storage role granted.
resource "google_storage_bucket_iam_member" "lore_corpus_writer" {
  bucket = google_storage_bucket.lore_corpus.name
  role   = "roles/storage.objectAdmin"
  member = "serviceAccount:${google_service_account.gha_deploy.email}"
}

# AC-3: archiviste-runtime (Cloud Run Job SA) needs read-only access to download
# the corpus at execution time. objectViewer = list + get, scoped to this bucket only.
resource "google_storage_bucket_iam_member" "lore_corpus_reader" {
  bucket = google_storage_bucket.lore_corpus.name
  role   = "roles/storage.objectViewer"
  member = "serviceAccount:${google_service_account.archiviste_runtime.email}"
}
