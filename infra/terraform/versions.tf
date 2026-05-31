terraform {
  # >= 1.7 required: tests/workers_iam.tftest.hcl uses override_resource blocks
  # which are a Terraform 1.7+ feature.
  required_version = ">= 1.7"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6"
    }
    cloudflare = {
      source  = "cloudflare/cloudflare"
      version = "~> 4"
    }
  }

  backend "gcs" {
    bucket = "archiviste-tf-state"
    prefix = "terraform/state"
  }
}
