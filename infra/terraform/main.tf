# PR-b adds only the Cloudflare provider.
# providers "google" / "google-beta" and locals.labels are declared in PR-a (main.tf).
provider "cloudflare" {
  api_token = var.cloudflare_api_token
}
