//! Shared application state passed to every handler.

use anyhow::Result;
use reqwest::Client;
use sqlx::PgPool;
use std::{sync::Arc, time::Duration};

use crate::{
    auth::{
        sessions::{PgSessionCreator, PgSessionRevoker, SessionCreator, SessionRevoker},
        throttle::ThrottleStore,
        user_lookup::{PgUserLookup, UserLookup},
    },
    config::Config,
    gcs::token::TokenProvider,
};

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
    /// In-process login throttle store (AC-7).
    ///
    /// Shared via `Arc` inside the `Arc<AppState>` so that the store survives
    /// request-handler boundaries.  Phase 1: single-replica `HashMap`.
    pub throttle: Arc<ThrottleStore>,
    /// User lookup abstraction (`email_is_taken`, `find_member`, `create_member`).
    ///
    /// `None` in test environments without a real DB.
    /// Production uses `PgUserLookup` backed by `db_pool`.
    /// Tests inject `InMemoryUserLookup` for handler happy-path coverage (M3 fix).
    pub user_lookup: Option<Arc<dyn UserLookup>>,
    /// Session creator abstraction (`create` session row).
    ///
    /// `None` in test environments without a real DB.
    /// Production uses `PgSessionCreator` backed by `db_pool`.
    /// Tests inject an in-memory implementation for AC-4 happy path (M3 fix).
    pub session_creator: Option<Arc<dyn SessionCreator>>,
    /// Session revoker abstraction (`revoke` session row).
    ///
    /// `None` in test environments without a real DB.
    /// Production uses `PgSessionRevoker` backed by `db_pool`.
    /// Tests inject an in-memory implementation for AC-8 happy path (N-5 fix).
    pub session_revoker: Option<Arc<dyn SessionRevoker>>,
    /// Bearer token provider for IAM `signBlob` calls (SEC-004 AC-3).
    ///
    /// Holds a dedicated HTTP client with 2 s / 5 s timeouts (AC-6).
    /// Shared across all signing requests via `Arc`; token is cached with
    /// refresh-ahead semantics inside `TokenProvider`.
    pub token_provider: Arc<TokenProvider>,
}

impl AppState {
    /// Build the application state from a loaded configuration.
    ///
    /// Constructs a `TokenProvider` pointing at the real Cloud Run metadata server.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP client or `TokenProvider` cannot be constructed.
    pub fn new(config: Config) -> Result<Self> {
        let http = Client::builder()
            .connect_timeout(Duration::from_millis(config.connect_timeout_ms))
            .timeout(Duration::from_millis(config.request_timeout_ms))
            .pool_idle_timeout(Duration::from_secs(90))
            .build()?;

        let token_provider = Arc::new(TokenProvider::new()?);

        Ok(Self {
            config,
            http,
            db_pool: None,
            throttle: Arc::new(ThrottleStore::new()),
            user_lookup: None,
            session_creator: None,
            session_revoker: None,
            token_provider,
        })
    }

    /// Build state with an already-constructed pool (production boot path).
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP client cannot be constructed.
    pub fn new_with_pool(config: Config, pool: PgPool) -> Result<Self> {
        let mut state = Self::new(config)?;
        let lookup: Arc<dyn UserLookup> = Arc::new(PgUserLookup(pool.clone()));
        let creator: Arc<dyn SessionCreator> = Arc::new(PgSessionCreator(pool.clone()));
        let revoker: Arc<dyn SessionRevoker> = Arc::new(PgSessionRevoker(pool.clone()));
        state.db_pool = Some(pool);
        state.user_lookup = Some(lookup);
        state.session_creator = Some(creator);
        state.session_revoker = Some(revoker);
        Ok(state)
    }

    /// Build state with custom `UserLookup` only (partial test injection path).
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP client cannot be constructed.
    #[cfg(any(test, feature = "test-utils"))]
    pub fn new_with_lookup(config: Config, lookup: Arc<dyn UserLookup>) -> Result<Self> {
        let mut state = Self::new(config)?;
        state.user_lookup = Some(lookup);
        Ok(state)
    }

    /// Build state with `UserLookup`, `SessionCreator`, and `SessionRevoker` (full test injection).
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP client cannot be constructed.
    #[cfg(any(test, feature = "test-utils"))]
    pub fn new_with_mocks(
        config: Config,
        lookup: Arc<dyn UserLookup>,
        creator: Arc<dyn SessionCreator>,
    ) -> Result<Self> {
        let mut state = Self::new(config)?;
        state.user_lookup = Some(lookup);
        state.session_creator = Some(creator);
        Ok(state)
    }

    /// Build state with all three test mocks including `SessionRevoker` (AC-8 test path).
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP client cannot be constructed.
    #[cfg(any(test, feature = "test-utils"))]
    pub fn new_with_all_mocks(
        config: Config,
        lookup: Arc<dyn UserLookup>,
        creator: Arc<dyn SessionCreator>,
        revoker: Arc<dyn SessionRevoker>,
    ) -> Result<Self> {
        let mut state = Self::new(config)?;
        state.user_lookup = Some(lookup);
        state.session_creator = Some(creator);
        state.session_revoker = Some(revoker);
        Ok(state)
    }

    /// Build state with an injected `TokenProvider` (test ctor for IAM mock injection).
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP client cannot be constructed.
    #[cfg(any(test, feature = "test-utils"))]
    pub fn new_with_token_provider(
        config: Config,
        token_provider: Arc<TokenProvider>,
    ) -> Result<Self> {
        let http = reqwest::Client::builder()
            .connect_timeout(Duration::from_millis(config.connect_timeout_ms))
            .timeout(Duration::from_millis(config.request_timeout_ms))
            .pool_idle_timeout(Duration::from_secs(90))
            .build()?;

        Ok(Self {
            config,
            http,
            db_pool: None,
            throttle: Arc::new(ThrottleStore::new()),
            user_lookup: None,
            session_creator: None,
            session_revoker: None,
            token_provider,
        })
    }

    /// Build state with a pool and an injected `TokenProvider` (DB + IAM mock injection).
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP client cannot be constructed.
    #[cfg(any(test, feature = "test-utils"))]
    pub fn new_with_pool_and_token_provider(
        config: Config,
        pool: PgPool,
        token_provider: Arc<TokenProvider>,
    ) -> Result<Self> {
        let mut state = Self::new_with_token_provider(config, token_provider)?;
        let lookup: Arc<dyn UserLookup> = Arc::new(PgUserLookup(pool.clone()));
        let creator: Arc<dyn SessionCreator> = Arc::new(PgSessionCreator(pool.clone()));
        let revoker: Arc<dyn SessionRevoker> = Arc::new(PgSessionRevoker(pool.clone()));
        state.db_pool = Some(pool);
        state.user_lookup = Some(lookup);
        state.session_creator = Some(creator);
        state.session_revoker = Some(revoker);
        Ok(state)
    }
}
