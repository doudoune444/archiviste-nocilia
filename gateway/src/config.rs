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
    /// Service-account email used as `X-Goog-Credential` in V4 signed URLs (AC-21).
    pub gcs_signing_sa_email: String,
    /// PKCS#8 RSA private key PEM for GCS V4 signing (AC-21, secret-hygiene: never logged).
    pub gcs_signing_private_key_pem: SecretString,
    /// GCS bucket name (e.g. `archiviste-conversations`).
    pub gcs_bucket: String,
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
            gcs_signing_sa_email: std::env::var("GCS_SIGNING_SA_EMAIL")
                .context("GCS_SIGNING_SA_EMAIL env var required")?,
            gcs_signing_private_key_pem: SecretString::from(
                std::env::var("GCS_SIGNING_PRIVATE_KEY_PEM")
                    .context("GCS_SIGNING_PRIVATE_KEY_PEM env var required")?,
            ),
            gcs_bucket: std::env::var("GCS_BUCKET").context("GCS_BUCKET env var required")?,
        })
    }
}
