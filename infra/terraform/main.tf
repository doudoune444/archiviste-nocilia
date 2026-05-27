provider "google" {
  project = var.project_id
  region  = var.region
  # user_project_override + billing_project = ADC user creds use this project for quota/billing.
  # Required by APIs like billingbudgets.googleapis.com which refuse calls without quota project.
  user_project_override = true
  billing_project       = var.project_id
}

provider "google-beta" {
  project               = var.project_id
  region                = var.region
  user_project_override = true
  billing_project       = var.project_id
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
