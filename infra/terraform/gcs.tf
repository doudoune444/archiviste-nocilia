# AC-4: GCS bucket for conversation persistence.
resource "google_storage_bucket" "conversations" {
  name                        = "archiviste-conversations"
  location                    = var.region
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"

  labels = local.labels

  lifecycle_rule {
    action {
      type = "Delete"
    }
    condition {
      age = 30
    }
  }
}
