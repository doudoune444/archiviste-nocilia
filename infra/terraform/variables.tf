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
