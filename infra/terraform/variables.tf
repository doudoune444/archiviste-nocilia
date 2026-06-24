variable "project_id" {
  description = "GCP project ID."
  type        = string
}

variable "region" {
  description = "GCP region for all managed resources."
  type        = string
  default     = "europe-west9"
}

variable "github_repo" {
  description = "GitHub repository in owner/name format for WIF CEL condition."
  type        = string
  default     = "doudoune444/archiviste-nocilia"
}

variable "domain" {
  description = "Primary public domain for the app."
  type        = string
  default     = "archiviste.nocilia.fr"
}

variable "billing_account" {
  description = "GCP billing account full name (e.g. billingAccounts/012345-ABCDEF-FEDCBA)."
  type        = string
}

variable "budget_email" {
  description = "Email address for billing budget notifications (must be a billing account admin)."
  type        = string
}

variable "cloudflare_account_id" {
  description = "Cloudflare account ID."
  type        = string
}

variable "cloudflare_api_token" {
  description = "Cloudflare API token. Permissions: Account > Workers Scripts:Edit; Zone > Workers Routes:Edit, Zone:Edit, DNS:Edit, Zone Settings:Edit, Page Rules:Edit, Single Redirect:Edit."
  type        = string
  sensitive   = true
}

# #275: public GCP unit prices (EUR) for the gateway cost-estimate endpoint
# (GET /v1/costs). Not secrets — plain env vars. The gateway has no hardcoded
# default fallback (security.md), so these must be supplied at boot.

variable "cost_postgres_instance_eur" {
  description = "Fixed monthly Cloud SQL instance price in EUR (cost-estimate model)."
  type        = string
  default     = "9.20"
}

variable "cost_postgres_storage_per_gb_eur" {
  description = "Cloud SQL storage price per GB-month in EUR (cost-estimate model)."
  type        = string
  default     = "0.17"
}

variable "cost_gcs_storage_per_gb_eur" {
  description = "GCS standard storage price per GB-month in EUR (cost-estimate model)."
  type        = string
  default     = "0.02"
}

variable "cost_workers_per_request_eur" {
  description = "Flat estimated cost per workers request in EUR (cost-estimate model)."
  type        = string
  default     = "0.0008"
}
