# PR-b variables: Cloudflare only.
# project_id, region, github_repo, domain, billing_account, budget_email are declared in PR-a (variables.tf).

variable "cloudflare_account_id" {
  description = "Cloudflare account ID."
  type        = string
}

variable "cloudflare_api_token" {
  description = "Cloudflare API token. Scopes: Zone:Edit, DNS:Edit, Page Rules:Edit, Bot Management:Edit."
  type        = string
  sensitive   = true
}
