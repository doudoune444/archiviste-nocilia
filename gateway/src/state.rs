//! Shared application state passed to every handler.

use anyhow::Result;
use reqwest::Client;

use crate::config::Config;

/// Process-wide state shared across handlers via Axum extractors.
pub struct AppState {
    /// Loaded runtime configuration.
    pub config: Config,
    /// HTTP client used for outbound calls to the workers tier.
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
            .timeout(std::time::Duration::from_secs(30))
            .build()?;
        Ok(Self { config, http })
    }
}
