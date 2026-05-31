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
    auth_metadata::{IdTokenProvider, TokenProvider},
    config::Config,
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
    /// GCS IAM signBlob token provider (SEC-004 / SEC-005 AC-3).
    ///
    /// Uses the default service-account scope.  Shared across all signing
    /// requests via `Arc`; token is cached with refresh-ahead semantics.
    pub gcs_token_provider: Arc<TokenProvider>,
    /// Cloud SQL IAM auth token provider (SEC-005 AC-3).
    ///
    /// Uses the `sqlservice.admin` scope.  Separate instance from
    /// `gcs_token_provider` — independent cache (AC-2).
    pub sql_token_provider: Arc<TokenProvider>,
    /// Google-signed ID-token provider for gateway→workers calls (SEC-006 AC-5).
    ///
    /// Audience = `config.workers_url` (Cloud Run service URL).  Cached with
    /// 60-second refresh-ahead semantics — independent cache from the OAuth
    /// access token providers above.
    pub workers_id_token_provider: Arc<IdTokenProvider>,
}

impl AppState {
    /// Build the application state from a loaded configuration.
    ///
    /// Constructs both `TokenProvider` instances pointing at the real Cloud Run
    /// metadata server.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP client or any `TokenProvider` cannot be constructed.
    pub fn new(config: Config) -> Result<Self> {
        let http = Client::builder()
            .connect_timeout(Duration::from_millis(config.connect_timeout_ms))
            .timeout(Duration::from_millis(config.request_timeout_ms))
            .pool_idle_timeout(Duration::from_secs(90))
            .build()?;

        let gcs_token_provider = Arc::new(TokenProvider::for_gcs_signing()?);
        let sql_token_provider = Arc::new(TokenProvider::for_cloud_sql()?);
        let workers_id_token_provider =
            Arc::new(IdTokenProvider::with_audience(config.workers_url.clone())?);

        Ok(Self {
            config,
            http,
            db_pool: None,
            throttle: Arc::new(ThrottleStore::new()),
            user_lookup: None,
            session_creator: None,
            session_revoker: None,
            gcs_token_provider,
            sql_token_provider,
            workers_id_token_provider,
        })
    }

    /// Build state with an already-constructed pool and SQL token provider (production boot path).
    ///
    /// The `sql_token_provider` is passed in because `run()` performs a fail-fast
    /// token fetch before pool construction; we reuse that same provider.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP client or GCS `TokenProvider` cannot be constructed.
    pub fn new_with_pool_and_sql_token_provider(
        config: Config,
        pool: PgPool,
        sql_token_provider: Arc<TokenProvider>,
    ) -> Result<Self> {
        let http = Client::builder()
            .connect_timeout(Duration::from_millis(config.connect_timeout_ms))
            .timeout(Duration::from_millis(config.request_timeout_ms))
            .pool_idle_timeout(Duration::from_secs(90))
            .build()?;

        let gcs_token_provider = Arc::new(TokenProvider::for_gcs_signing()?);
        let workers_id_token_provider =
            Arc::new(IdTokenProvider::with_audience(config.workers_url.clone())?);

        let lookup: Arc<dyn UserLookup> = Arc::new(PgUserLookup(pool.clone()));
        let creator: Arc<dyn SessionCreator> = Arc::new(PgSessionCreator(pool.clone()));
        let revoker: Arc<dyn SessionRevoker> = Arc::new(PgSessionRevoker(pool.clone()));

        Ok(Self {
            config,
            http,
            db_pool: Some(pool),
            throttle: Arc::new(ThrottleStore::new()),
            user_lookup: Some(lookup),
            session_creator: Some(creator),
            session_revoker: Some(revoker),
            gcs_token_provider,
            sql_token_provider,
            workers_id_token_provider,
        })
    }

    /// Build state with an already-constructed pool (legacy test helper, deprecated for production).
    ///
    /// Uses default metadata-server providers for both GCS and SQL scopes.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP client cannot be constructed.
    pub fn new_with_pool(config: Config, pool: PgPool) -> Result<Self> {
        let sql_token_provider = Arc::new(TokenProvider::for_cloud_sql()?);
        Self::new_with_pool_and_sql_token_provider(config, pool, sql_token_provider)
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

    /// Build state with injected GCS `TokenProvider` (test ctor for IAM mock injection).
    ///
    /// Sets only `gcs_token_provider`; `sql_token_provider` uses the default metadata server.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP client cannot be constructed.
    #[cfg(any(test, feature = "test-utils"))]
    pub fn new_with_token_provider(
        config: Config,
        gcs_token_provider: Arc<TokenProvider>,
    ) -> Result<Self> {
        let http = reqwest::Client::builder()
            .connect_timeout(Duration::from_millis(config.connect_timeout_ms))
            .timeout(Duration::from_millis(config.request_timeout_ms))
            .pool_idle_timeout(Duration::from_secs(90))
            .build()?;

        let sql_token_provider = Arc::new(TokenProvider::for_cloud_sql()?);
        let workers_id_token_provider =
            Arc::new(IdTokenProvider::with_audience(config.workers_url.clone())?);

        Ok(Self {
            config,
            http,
            db_pool: None,
            throttle: Arc::new(ThrottleStore::new()),
            user_lookup: None,
            session_creator: None,
            session_revoker: None,
            gcs_token_provider,
            sql_token_provider,
            workers_id_token_provider,
        })
    }

    /// Build state with an injected `IdTokenProvider` for workers ID-token mock injection (SEC-006).
    ///
    /// Used by `test_chat_workers_auth.rs` to point the ID-token fetch at a mockito server
    /// instead of the real Cloud Run metadata server.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP client cannot be constructed.
    #[cfg(any(test, feature = "test-utils"))]
    pub fn new_with_id_token_provider(
        config: Config,
        workers_id_token_provider: Arc<IdTokenProvider>,
    ) -> Result<Self> {
        let http = reqwest::Client::builder()
            .connect_timeout(Duration::from_millis(config.connect_timeout_ms))
            .timeout(Duration::from_millis(config.request_timeout_ms))
            .pool_idle_timeout(Duration::from_secs(90))
            .build()?;

        let gcs_token_provider = Arc::new(TokenProvider::for_gcs_signing()?);
        let sql_token_provider = Arc::new(TokenProvider::for_cloud_sql()?);

        Ok(Self {
            config,
            http,
            db_pool: None,
            throttle: Arc::new(ThrottleStore::new()),
            user_lookup: None,
            session_creator: None,
            session_revoker: None,
            gcs_token_provider,
            sql_token_provider,
            workers_id_token_provider,
        })
    }

    /// Build state with a pool and an injected GCS `TokenProvider` (DB + IAM mock injection).
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP client cannot be constructed.
    #[cfg(any(test, feature = "test-utils"))]
    pub fn new_with_pool_and_token_provider(
        config: Config,
        pool: PgPool,
        gcs_token_provider: Arc<TokenProvider>,
    ) -> Result<Self> {
        let mut state = Self::new_with_token_provider(config, gcs_token_provider)?;
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
