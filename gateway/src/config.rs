//! Runtime configuration loaded from environment variables.

use anyhow::{Context, Result};
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
    /// Crate version string surfaced via `/healthz`.
    pub version: String,
}

impl Config {
    /// Read configuration from process environment.
    ///
    /// `BIND_ADDR` defaults to `0.0.0.0:8080`. `WORKERS_URL` and
    /// `DATABASE_URL` are required.
    ///
    /// # Errors
    ///
    /// Returns an error if a required environment variable is missing.
    pub fn from_env() -> Result<Self> {
        Ok(Self {
            bind_addr: std::env::var("BIND_ADDR").unwrap_or_else(|_| "0.0.0.0:8080".to_string()),
            workers_url: std::env::var("WORKERS_URL").context("WORKERS_URL env var required")?,
            database_url: std::env::var("DATABASE_URL").context("DATABASE_URL env var required")?,
            version: env!("CARGO_PKG_VERSION").to_string(),
        })
    }
}
