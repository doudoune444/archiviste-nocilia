//! Shared application state passed to every handler.

use anyhow::Result;
use reqwest::Client;
use sqlx::PgPool;
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
    /// `PostgreSQL` connection pool (optional for tests without a real DB).
    ///
    /// `None` in test environments that do not provide a real `DATABASE_URL`.
    /// Production always has a pool; absence causes session checks to fail
    /// gracefully (401 `session_revoked`).
    pub db_pool: Option<PgPool>,
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

        // Pool is omitted in unit/integration tests that don't supply a real URL.
        // Production code calls `new_with_pool` or `run()` which initialises the pool.
        Ok(Self {
            config,
            http,
            db_pool: None,
        })
    }

    /// Build state with an already-constructed pool (production boot path).
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP client cannot be constructed.
    pub fn new_with_pool(config: Config, pool: PgPool) -> Result<Self> {
        let mut state = Self::new(config)?;
        state.db_pool = Some(pool);
        Ok(state)
    }
}
