# AC-8: Cloudflare zone settings, DNS, rate-limit, and redirect page rules.

data "cloudflare_zone" "nocilia_fr" {
  name       = "nocilia.fr"
  account_id = var.cloudflare_account_id
}

data "cloudflare_zone" "nocilia_com" {
  name       = "nocilia.com"
  account_id = var.cloudflare_account_id
}

data "cloudflare_zone" "nocilia_org" {
  name       = "nocilia.org"
  account_id = var.cloudflare_account_id
}

data "cloudflare_zone" "nocilia_eu" {
  name       = "nocilia.eu"
  account_id = var.cloudflare_account_id
}

data "cloudflare_zone" "nocilia_net" {
  name       = "nocilia.net"
  account_id = var.cloudflare_account_id
}

# DNS: archiviste.nocilia.fr → Cloud Run gateway (proxied through Cloudflare).
#
# Region constraint: google_cloud_run_domain_mapping is NOT available in europe-west9.
# Supported regions are limited (us-central1, us-east1/4, us-west1, europe-west1,
# asia-east1, asia-northeast1). Rather than relocate the whole stack to europe-west1,
# we drop the domain mapping entirely and rely on Cloudflare as the reverse proxy:
#  - CNAME archiviste.nocilia.fr → <gateway>.run.app (the actual Cloud Run hostname)
#  - Cloudflare proxied (orange cloud) terminates TLS at edge with its own cert
#  - Origin connection: CF → Cloud Run over HTTPS, SNI = *.run.app (Google cert)
#  - Cloud Run service uses INGRESS_TRAFFIC_ALL. Its public frontend routes by Host
#    header and has NO domain mapping for archiviste.nocilia.fr (mappings unavailable
#    in europe-west9), so a forwarded visitor Host of archiviste.nocilia.fr 404s at
#    the frontend before reaching the gateway. The Origin Rule below rewrites the
#    Host sent to origin to the <gateway>.run.app hostname so the frontend routes
#    the request to the gateway service.
# This is the documented Cloud Run + Cloudflare integration pattern when domain
# mappings are unavailable. No GLB / Serverless NEG cost overhead.
resource "cloudflare_record" "archiviste_fr" {
  zone_id = data.cloudflare_zone.nocilia_fr.id
  name    = "archiviste"
  type    = "CNAME"
  # Strip "https://" scheme from gateway.uri to get bare hostname for CNAME content.
  content = replace(google_cloud_run_v2_service.gateway.uri, "https://", "")
  proxied = true
}

# Origin Rule: override the Host header sent to the Cloud Run origin.
# Cloudflare proxied mode forwards the visitor Host (archiviste.nocilia.fr) by default;
# Cloud Run's frontend has no domain mapping for it and 404s. Rewriting Host to the
# <gateway>.run.app hostname (same value as the CNAME content) makes the frontend
# route to the gateway service. http_request_origin phase = Origin Rules (Free plan).
resource "cloudflare_ruleset" "archiviste_fr_origin_host" {
  zone_id     = data.cloudflare_zone.nocilia_fr.id
  name        = "Override origin Host for archiviste.nocilia.fr"
  description = "Rewrite Host to <gateway>.run.app so Cloud Run frontend routes to the gateway"
  kind        = "zone"
  phase       = "http_request_origin"

  rules {
    action = "route"
    action_parameters {
      host_header = replace(google_cloud_run_v2_service.gateway.uri, "https://", "")
    }
    expression  = "(http.host eq \"archiviste.nocilia.fr\")"
    description = "Set origin Host to the Cloud Run gateway hostname"
    enabled     = true
  }
}

# AC-8: TLS Full Strict + Security Level medium + Challenge TTL + Brotli + HTTPS upgrade.
# ssl = "strict" is the provider v4 value for Cloudflare "Full (strict)" mode.
# Bot Fight Mode is NOT managed here: Cloudflare provider 4.52+ removed `bot_fight_mode`
# from cloudflare_zone_settings_override (moved to cloudflare_bot_management on paid plans
# only; Free plan = manual UI toggle). Operator enables it once via CF UI per
# docs/runbook/bootstrap-gcp.md step 11. Spec AC-8 amended — see docs/blockers.md
# 2026-05-27 INFRA-002 entry.
resource "cloudflare_zone_settings_override" "nocilia_fr" {
  zone_id = data.cloudflare_zone.nocilia_fr.id

  settings {
    ssl              = "strict"
    security_level   = "medium"
    challenge_ttl    = 1800
    brotli           = "on"
    always_use_https = "on"
  }
}

# Rate-limit: NOT managed here. `cloudflare_rate_limit` resource is deprecated since
# June 2025 (11+ months past EOL by 2026-05). Modern replacement = `cloudflare_ruleset`
# http_ratelimit phase, deferred to V2 SEC-002 which adds app-level tower_governor +
# Redis sliding window (CF perimeter rate-limit becomes redundant). For V1, operator
# configures 1 rule manually via CF UI per docs/runbook/bootstrap-gcp.md step 12.
# Spec AC-8 amended in scope — see docs/blockers.md 2026-05-27 INFRA-002 entry.

# AC-8: 4 × 301 redirects archiviste.nocilia.{com,org,eu,net} → https://archiviste.nocilia.fr/$1.
# Cloudflare free plan quota = 3 Page Rules per zone.
# .com / .org / .eu use cloudflare_page_rule (1 rule each, within quota).
# .net uses cloudflare_ruleset http_request_dynamic_redirect (modern replacement, no Page Rule quota).
resource "cloudflare_page_rule" "redirect_com" {
  zone_id  = data.cloudflare_zone.nocilia_com.id
  target   = "archiviste.nocilia.com/*"
  priority = 1

  actions {
    forwarding_url {
      url         = "https://archiviste.nocilia.fr/$1"
      status_code = 301
    }
  }
}

resource "cloudflare_page_rule" "redirect_org" {
  zone_id  = data.cloudflare_zone.nocilia_org.id
  target   = "archiviste.nocilia.org/*"
  priority = 1

  actions {
    forwarding_url {
      url         = "https://archiviste.nocilia.fr/$1"
      status_code = 301
    }
  }
}

resource "cloudflare_page_rule" "redirect_eu" {
  zone_id  = data.cloudflare_zone.nocilia_eu.id
  target   = "archiviste.nocilia.eu/*"
  priority = 1

  actions {
    forwarding_url {
      url         = "https://archiviste.nocilia.fr/$1"
      status_code = 301
    }
  }
}

# R2 mitigation: .net redirect via cloudflare_ruleset to avoid exceeding free-plan Page Rules quota (max 3).
resource "cloudflare_ruleset" "redirect_net" {
  zone_id     = data.cloudflare_zone.nocilia_net.id
  name        = "Redirect archiviste.nocilia.net to .fr"
  description = "301 archiviste.nocilia.net/* → https://archiviste.nocilia.fr/$1"
  kind        = "zone"
  phase       = "http_request_dynamic_redirect"

  rules {
    action = "redirect"
    action_parameters {
      from_value {
        status_code = 301
        target_url {
          expression = "concat(\"https://archiviste.nocilia.fr\", http.request.uri.path)"
        }
        preserve_query_string = true
      }
    }
    expression  = "(http.host eq \"archiviste.nocilia.net\")"
    description = "301 archiviste.nocilia.net → archiviste.nocilia.fr"
    enabled     = true
  }
}
