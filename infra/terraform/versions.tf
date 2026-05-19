terraform {
  required_version = ">= 1.6"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 6"
    }
    google-beta = {
      source  = "hashicorp/google-beta"
      version = "~> 6"
    }
  }

  backend "gcs" {
    bucket = "archiviste-tf-state"
    prefix = "terraform/state"
  }
}
