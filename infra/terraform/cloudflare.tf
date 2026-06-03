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
#  - CNAME archiviste.nocilia.fr → <gateway>.run.app, Cloudflare proxied (orange cloud)
#  - Cloudflare terminates TLS at edge with its own cert
#  - A Worker route on archiviste.nocilia.fr/* rewrites the request to the
#    <gateway>.run.app origin (see cloudflare_workers_script below).
# Why a Worker and not an Origin Rule "Host Header Override": Cloud Run's frontend
# routes by Host header and 404s on the unknown archiviste.nocilia.fr Host. The
# Origin Rule that fixes this requires a PAID Cloudflare plan (Free returns
# "not entitled to use the HostHeader override" — see docs/blockers.md 2026-06-03).
# The Worker does the same Host rewrite on the Free plan. No GLB / NEG cost overhead.
resource "cloudflare_record" "archiviste_fr" {
  zone_id = data.cloudflare_zone.nocilia_fr.id
  name    = "archiviste"
  type    = "CNAME"
  # Strip "https://" scheme from gateway.uri to get bare hostname for CNAME content.
  content = replace(google_cloud_run_v2_service.gateway.uri, "https://", "")
  proxied = true
}

# Worker reverse-proxy: rewrites archiviste.nocilia.fr requests to the
# <gateway>.run.app origin so Cloud Run's Host-based frontend routes to the gateway.
# Replaces the paid-plan-only Origin Rule (see comment above). ORIGIN_HOST is the
# single source of truth for the run.app hostname, shared with the CNAME content.
resource "cloudflare_workers_script" "host_proxy" {
  account_id = var.cloudflare_account_id
  name       = "archiviste-host-proxy"
  content    = file("${path.module}/workers/host-proxy.js")
  module     = true

  plain_text_binding {
    name = "ORIGIN_HOST"
    text = replace(google_cloud_run_v2_service.gateway.uri, "https://", "")
  }
}

# Bind the Worker to every path on the apex host. The proxied CNAME above puts
# archiviste.nocilia.fr behind Cloudflare so this route can intercept.
resource "cloudflare_workers_route" "archiviste_fr" {
  zone_id     = data.cloudflare_zone.nocilia_fr.id
  pattern     = "archiviste.nocilia.fr/*"
  script_name = cloudflare_workers_script.host_proxy.name
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
