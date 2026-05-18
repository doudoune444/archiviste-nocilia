# PR-b appends provider cloudflare/cloudflare ~> 4 to the required_providers block.
# The terraform{} block, required_version, backend "gcs", google, and google-beta providers
# are declared in PR-a (versions.tf). This file will produce a merge conflict with PR-a if applied
# standalone; it is only valid after PR-a is merged (order: a → b, per plan D-1).

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
