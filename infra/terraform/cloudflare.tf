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
  value   = "ghs.googlehosted.com"
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

# AC-8: TLS Full Strict + Bot Fight Mode ON + Security Level medium + Challenge TTL.
# ssl = "strict" is the provider v4 value for Cloudflare "Full (strict)" mode.
# bot_fight_mode = "on" requires Zone:Bot Management scope on the API token.
resource "cloudflare_zone_settings_override" "nocilia_fr" {
  zone_id = data.cloudflare_zone.nocilia_fr.id

  settings {
    ssl              = "strict"
    security_level   = "medium"
    challenge_ttl    = 1800
    brotli           = "on"
    always_use_https = "on"
    bot_fight_mode   = "on"
  }
}

# Rate-limit rule: 100 req/min/IP on archiviste.nocilia.fr.
resource "cloudflare_rate_limit" "archiviste_fr" {
  zone_id   = data.cloudflare_zone.nocilia_fr.id
  threshold = 100
  period    = 60
  description = "100 req/min/IP on archiviste.nocilia.fr"

  match {
    request {
      url_pattern = "archiviste.nocilia.fr/*"
      schemes     = ["HTTPS"]
    }
  }

  action {
    mode    = "challenge"
    timeout = 300
  }
}

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
