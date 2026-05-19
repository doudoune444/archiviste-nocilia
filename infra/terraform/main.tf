provider "google" {
  project = var.project_id
  region  = var.region
}

provider "google-beta" {
  project = var.project_id
  region  = var.region
}

locals {
  labels = {
    app     = "archiviste"
    env     = "beta"
    managed = "terraform"
  }
}

provider "cloudflare" {
  api_token = var.cloudflare_api_token
}
