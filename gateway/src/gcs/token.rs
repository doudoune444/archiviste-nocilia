//! Cloud Run metadata-server bearer token provider for IAM signBlob.
//!
//! `TokenProvider` fetches an OAuth access token from the Cloud Run metadata
//! server and caches it with refresh-ahead semantics (AC-3).
//!
//! The metadata base URL is injectable for testing; production uses the
//! standard Google Cloud endpoint.

use chrono::{DateTime, Duration, Utc};
use secrecy::SecretString;
use thiserror::Error;
use tokio::sync::RwLock;

/// Path to the default service-account token on the GCE/Cloud Run metadata server.
const METADATA_TOKEN_PATH: &str = "/computeMetadata/v1/instance/service-accounts/default/token";

/// Seconds before expiry at which the token is considered stale and refreshed proactively.
const REFRESH_AHEAD_SECS: i64 = 60;

/// Errors from token operations.
#[derive(Debug, Error)]
pub enum TokenError {
    /// Metadata server returned a non-2xx response or an unparseable body.
    #[error("metadata token fetch failed")]
    Fetch,
    /// Request timed out.
    #[error("timeout")]
    Timeout,
    /// Network-level failure (DNS, TCP, TLS).
    #[error("network")]
    Network,
}

/// Raw response from the metadata-server token endpoint.
#[derive(serde::Deserialize)]
struct MetadataTokenResponse {
    access_token: String,
    expires_in: i64,
}

/// An active cached bearer token.
struct CachedToken {
    bearer: SecretString,
    expires_at: DateTime<Utc>,
}

/// Thread-safe bearer token provider with refresh-ahead cache (AC-3).
///
/// Holds a dedicated `reqwest::Client` with 2 s connect / 5 s total timeouts
/// (AC-6). The metadata base URL is injectable for testing.
pub struct TokenProvider {
    client: reqwest::Client,
    metadata_base_url: String,
    cache: RwLock<Option<CachedToken>>,
}

impl TokenProvider {
    /// Build a `TokenProvider` pointing at the real Cloud Run metadata server.
    ///
    /// # Errors
    /// Returns an error if the HTTP client cannot be constructed.
    pub fn new() -> Result<Self, TokenError> {
        Self::with_base_url("http://metadata.google.internal".to_string())
    }

    /// Build a `TokenProvider` with an injectable metadata base URL (test ctor).
    ///
    /// # Errors
    /// Returns an error if the HTTP client cannot be constructed.
    pub fn with_base_url(metadata_base_url: String) -> Result<Self, TokenError> {
        let client = reqwest::Client::builder()
            .connect_timeout(std::time::Duration::from_secs(
                crate::gcs::sign::CONNECT_TIMEOUT_SECS,
            ))
            .timeout(std::time::Duration::from_secs(
                crate::gcs::sign::TOTAL_TIMEOUT_SECS,
            ))
            .build()
            .map_err(|_| TokenError::Network)?;

        Ok(Self {
            client,
            metadata_base_url,
            cache: RwLock::new(None),
        })
    }

    /// Return a valid bearer token, refreshing the cache if necessary.
    ///
    /// First call = fetch lazy. Subsequent calls reuse the cache until the
    /// token enters the refresh-ahead window (`now >= expires_at - 60 s`).
    /// On a `401` from `signBlob`, call `invalidate` and retry — see
    /// `sign::sign_get` for the retry logic.
    ///
    /// # Errors
    /// Returns `TokenError` on fetch failure.
    pub async fn get_or_refresh(&self) -> Result<SecretString, TokenError> {
        // Fast path: read-lock only.
        {
            let guard = self.cache.read().await;
            if let Some(cached) = guard.as_ref() {
                if Utc::now() < cached.expires_at - Duration::seconds(REFRESH_AHEAD_SECS) {
                    return Ok(cached.bearer.clone());
                }
            }
        }
        // Slow path: write-lock + double-check.
        let mut guard = self.cache.write().await;
        if let Some(cached) = guard.as_ref() {
            if Utc::now() < cached.expires_at - Duration::seconds(REFRESH_AHEAD_SECS) {
                return Ok(cached.bearer.clone());
            }
        }
        let fresh = self.fetch_token().await?;
        let bearer = fresh.bearer.clone();
        *guard = Some(fresh);
        Ok(bearer)
    }

    /// Evict the cached token so the next `get_or_refresh` forces a fresh fetch.
    ///
    /// Called by `sign_get` after receiving a `401` from `signBlob` (AC-3 retry-on-401).
    pub async fn invalidate(&self) {
        *self.cache.write().await = None;
    }

    /// Fetch a fresh token from the metadata server.
    async fn fetch_token(&self) -> Result<CachedToken, TokenError> {
        let url = format!("{}{}", self.metadata_base_url, METADATA_TOKEN_PATH);

        let resp = self
            .client
            .get(&url)
            .header("Metadata-Flavor", "Google")
            .send()
            .await
            .map_err(|e| classify_reqwest_error(&e))?;

        if !resp.status().is_success() {
            return Err(TokenError::Fetch);
        }

        let body: MetadataTokenResponse = resp.json().await.map_err(|_| TokenError::Fetch)?;

        Ok(CachedToken {
            bearer: SecretString::from(body.access_token),
            expires_at: Utc::now() + Duration::seconds(body.expires_in),
        })
    }
}

/// Map a `reqwest::Error` to a `TokenError`.
pub(crate) fn classify_reqwest_error(e: &reqwest::Error) -> TokenError {
    if e.is_timeout() {
        TokenError::Timeout
    } else {
        TokenError::Network
    }
}
