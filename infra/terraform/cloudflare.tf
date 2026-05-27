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
resource "cloudflare_record" "archiviste_fr" {
  zone_id = data.cloudflare_zone.nocilia_fr.id
  name    = "archiviste"
  type    = "CNAME"
  content = "ghs.googlehosted.com"
  proxied = true
}

# Cloud Run custom domain mapping for gateway.
resource "google_cloud_run_domain_mapping" "archiviste_fr" {
  location = var.region
  name     = var.domain

  metadata {
    namespace = var.project_id
  }

  spec {
    route_name = google_cloud_run_v2_service.gateway.name
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
