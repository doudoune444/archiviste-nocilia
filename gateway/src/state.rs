//! Shared application state passed to every handler.

use anyhow::Result;
use reqwest::Client;
use std::time::Duration;

use crate::config::Config;

/// Process-wide state shared across handlers via Axum extractors.
pub struct AppState {
    /// Loaded runtime configuration.
    pub config: Config,
    /// HTTP client used for outbound calls to the workers tier.
    ///
    /// Single instance shared across all requests (keep-alive pool).
    /// Timeouts are set from `Config.connect_timeout_ms` / `Config.request_timeout_ms`.
    pub http: Client,
}

impl AppState {
    /// Build the application state from a loaded configuration.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP client cannot be constructed.
    pub fn new(config: Config) -> Result<Self> {
        let http = Client::builder()
            .connect_timeout(Duration::from_millis(config.connect_timeout_ms))
            .timeout(Duration::from_millis(config.request_timeout_ms))
            .pool_idle_timeout(Duration::from_secs(90))
            .build()?;
        Ok(Self { config, http })
    }
}
