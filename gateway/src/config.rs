//! Runtime configuration loaded from environment variables.

use anyhow::{Context, Result};
use secrecy::SecretString;
use serde::Deserialize;

/// Gateway runtime configuration.
#[derive(Debug, Clone, Deserialize)]
pub struct Config {
    /// `host:port` the HTTP server binds to.
    pub bind_addr: String,
    /// Base URL of the internal workers tier (e.g. `http://workers:8000`).
    pub workers_url: String,
    /// `PostgreSQL` connection string (`sqlx`-compatible).
    pub database_url: String,
    /// Ed25519 public key PEM for JWT verification (AC-5, AC-12).
    ///
    /// Required at boot. Gateway refuses to start if absent or malformed
    /// (failure mode "clé absente → refus démarrage").
    pub jwt_ed25519_public_key_pem: String,
    /// Ed25519 private key PEM for JWT signing (AC-5, PR-b).
    ///
    /// Typed `SecretString` — never logged or exposed in debug output (`security.md` §A09).
    /// Required at boot; gateway refuses to start if absent.
    pub jwt_ed25519_private_key_pem: SecretString,
    /// Key ID embedded in signed JWTs (`kid` header claim — AC-5).
    ///
    /// Defaults to `"default"`. Override via `JWT_KID` env var for manual key rotation.
    pub jwt_kid: String,
    /// Crate version string surfaced via `/healthz`.
    pub version: String,
    /// TCP connect timeout for outbound calls to workers (milliseconds).
    /// Defaults to 5 000 ms. Override via `CONNECT_TIMEOUT_MS` env var.
    /// In tests, set to a low value to keep AC-9 fast.
    pub connect_timeout_ms: u64,
    /// Total request timeout for outbound calls to workers (milliseconds).
    /// Defaults to 35 000 ms. Override via `REQUEST_TIMEOUT_MS` env var.
    /// In tests, set to a low value (e.g. 200) to test AC-8 without waiting 35 s.
    pub request_timeout_ms: u64,
    /// Per-call read timeout for the worker call in `POST /v1/chat` (milliseconds).
    ///
    /// Applied as a `RequestBuilder::timeout` override ONLY on the chat path
    /// (#294); the global client and every other worker route keep
    /// `request_timeout_ms`. Defaults to 90 000 ms — a worst-case upper bound on
    /// a worker cold start (transformers import > 30 s at boot) plus LLM
    /// generation, so the call is not severed at the 35 s ceiling and turned into
    /// a spurious 504. Override via `CHAT_REQUEST_TIMEOUT_MS`; in tests set low to
    /// exercise the chat timeout without waiting 90 s.
    pub chat_request_timeout_ms: u64,
    /// Service-account email used as `X-Goog-Credential` in V4 signed URLs (AC-21).
    /// Signing is performed via IAM `signBlob` — no SA private key required (SEC-004 AC-1).
    pub gcs_signing_sa_email: String,
    /// GCS bucket name (e.g. `archiviste-conversations`).
    pub gcs_bucket: String,
    /// Full Cloud Run Admin API v2 URL of the workers service descriptor (#253).
    ///
    /// Example:
    /// `https://run.googleapis.com/v2/projects/<p>/locations/<l>/services/<svc>`.
    /// The gateway issues an authenticated read-only GET against this URL to read
    /// the `Ready` condition of the latest revision — an out-of-band signal that
    /// does NOT wake the scale-to-zero service.  Injectable for testing.
    pub cloud_run_service_url: String,
    /// Public GCP unit prices (EUR) for the cost estimate (#275).
    ///
    /// Loaded at boot from env vars with no hardcoded default fallback
    /// (security.md — `getenv(KEY, "default")` forbidden). `None` when the
    /// tariff configuration is entirely absent: the gateway still boots, but
    /// `GET /v1/costs` then returns a sanitized error instead of a fabricated
    /// amount (#277). A partial or malformed tariff set is still a boot error.
    pub cost_tariffs: Option<CostTariffs>,
}

/// Public GCP unit prices in EUR for the cost-estimate model (#275).
///
/// Faithful to the model: fixed Postgres instance price, Cloud SQL storage
/// price per GB, GCS storage price per GB, and flat cost per workers request.
#[derive(Debug, Clone, Copy, PartialEq, Deserialize)]
pub struct CostTariffs {
    /// Fixed monthly Cloud SQL instance price (`prix_instance_fixe`).
    pub postgres_instance_eur: f64,
    /// Cloud SQL storage price per GB-month (`prix_stockage_Go`).
    pub postgres_storage_per_gb_eur: f64,
    /// GCS storage price per GB-month (`prix_stockage_Go`).
    pub gcs_storage_per_gb_eur: f64,
    /// Flat cost per workers request (`coût_par_requête`).
    pub workers_per_request_eur: f64,
}

impl CostTariffs {
    /// Read every tariff from its env var as a finite non-negative `f64`.
    ///
    /// # Errors
    ///
    /// Returns an error if any tariff env var is missing or unparsable. There is
    /// no default fallback (security.md).
    pub fn from_env() -> Result<Self> {
        Ok(Self {
            postgres_instance_eur: read_tariff_var("COST_POSTGRES_INSTANCE_EUR")?,
            postgres_storage_per_gb_eur: read_tariff_var("COST_POSTGRES_STORAGE_PER_GB_EUR")?,
            gcs_storage_per_gb_eur: read_tariff_var("COST_GCS_STORAGE_PER_GB_EUR")?,
            workers_per_request_eur: read_tariff_var("COST_WORKERS_PER_REQUEST_EUR")?,
        })
    }

    /// Read the tariff set, tolerating a *fully absent* configuration (#277).
    ///
    /// - All four tariff env vars absent → `Ok(None)`: the gateway boots and
    ///   `GET /v1/costs` returns a sanitized error at request time.
    /// - All four present and valid → `Ok(Some(_))`.
    /// - Any present (operator intended to configure costs) but the set is
    ///   incomplete or unparsable → `Err`: that is a misconfiguration, not an
    ///   absent feature, so the gateway refuses to start.
    ///
    /// # Errors
    ///
    /// Returns an error on a partial or malformed tariff set.
    pub fn from_env_optional() -> Result<Option<Self>> {
        const VARS: [&str; 4] = [
            "COST_POSTGRES_INSTANCE_EUR",
            "COST_POSTGRES_STORAGE_PER_GB_EUR",
            "COST_GCS_STORAGE_PER_GB_EUR",
            "COST_WORKERS_PER_REQUEST_EUR",
        ];
        let present_count = VARS.iter().filter(|v| std::env::var(v).is_ok()).count();
        if present_count == 0 {
            return Ok(None);
        }
        Self::from_env().map(Some)
    }
}

/// Zero-valued tariffs for tests that do not exercise the cost endpoint.
///
/// Only available under the `test-utils` feature — production always loads
/// real tariffs via [`CostTariffs::from_env`] (no default fallback, security.md).
#[cfg(feature = "test-utils")]
impl Default for CostTariffs {
    fn default() -> Self {
        Self {
            postgres_instance_eur: 0.0,
            postgres_storage_per_gb_eur: 0.0,
            gcs_storage_per_gb_eur: 0.0,
            workers_per_request_eur: 0.0,
        }
    }
}

/// Parse one tariff env var as a finite, non-negative `f64`.
fn read_tariff_var(name: &str) -> Result<f64> {
    let raw = std::env::var(name).with_context(|| format!("{name} env var required"))?;
    let value: f64 = raw
        .parse()
        .with_context(|| format!("{name} must be a number, got: {raw}"))?;
    if value.is_finite() && value >= 0.0 {
        Ok(value)
    } else {
        anyhow::bail!("{name} must be a finite non-negative number, got: {raw}")
    }
}

impl Config {
    /// Read configuration from process environment.
    ///
    /// `BIND_ADDR` defaults to `0.0.0.0:8080`. `WORKERS_URL`, `DATABASE_URL`,
    /// and `JWT_ED25519_PUBLIC_KEY_PEM` are required. `CONNECT_TIMEOUT_MS` and
    /// `REQUEST_TIMEOUT_MS` are optional (defaults: 5000 / 35000).
    ///
    /// # Errors
    ///
    /// Returns an error if a required environment variable is missing.
    pub fn from_env() -> Result<Self> {
        Ok(Self {
            bind_addr: std::env::var("BIND_ADDR").unwrap_or_else(|_| "0.0.0.0:8080".to_string()),
            workers_url: std::env::var("WORKERS_URL").context("WORKERS_URL env var required")?,
            database_url: std::env::var("DATABASE_URL").context("DATABASE_URL env var required")?,
            jwt_ed25519_public_key_pem: std::env::var("JWT_ED25519_PUBLIC_KEY_PEM")
                .context("JWT_ED25519_PUBLIC_KEY_PEM env var required")?,
            jwt_ed25519_private_key_pem: SecretString::from(
                std::env::var("JWT_ED25519_PRIVATE_KEY_PEM")
                    .context("JWT_ED25519_PRIVATE_KEY_PEM env var required")?,
            ),
            jwt_kid: std::env::var("JWT_KID").unwrap_or_else(|_| "default".to_string()),
            version: env!("CARGO_PKG_VERSION").to_string(),
            connect_timeout_ms: std::env::var("CONNECT_TIMEOUT_MS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(5_000),
            request_timeout_ms: std::env::var("REQUEST_TIMEOUT_MS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(35_000),
            chat_request_timeout_ms: std::env::var("CHAT_REQUEST_TIMEOUT_MS")
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(90_000),
            gcs_signing_sa_email: std::env::var("GCS_SIGNING_SA_EMAIL")
                .context("GCS_SIGNING_SA_EMAIL env var required")?,
            gcs_bucket: std::env::var("GCS_BUCKET").context("GCS_BUCKET env var required")?,
            cloud_run_service_url: std::env::var("CLOUD_RUN_SERVICE_URL")
                .context("CLOUD_RUN_SERVICE_URL env var required")?,
            cost_tariffs: CostTariffs::from_env_optional()?,
        })
    }
}
