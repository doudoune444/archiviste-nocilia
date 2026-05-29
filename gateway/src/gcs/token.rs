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

/// TCP connect timeout for the signing HTTP client (AC-6).
///
/// Shared by `TokenProvider` (metadata fetch) and `sign_get` (signBlob POST)
/// via `TokenProvider::client()` — a single shared client for both call legs.
pub(crate) const CONNECT_TIMEOUT_SECS: u64 = 2;

/// Total request timeout for the signing HTTP client (AC-6).
///
/// Shared by `TokenProvider` (metadata fetch) and `sign_get` (signBlob POST)
/// via `TokenProvider::client()` — a single shared client for both call legs.
pub(crate) const TOTAL_TIMEOUT_SECS: u64 = 5;

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
/// (AC-6). The same client is reused for both metadata token fetches and
/// signBlob POST calls via `TokenProvider::client()`. The metadata base URL
/// is injectable for testing.
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
            .connect_timeout(std::time::Duration::from_secs(CONNECT_TIMEOUT_SECS))
            .timeout(std::time::Duration::from_secs(TOTAL_TIMEOUT_SECS))
            .build()
            .map_err(|_| TokenError::Network)?;

        Ok(Self {
            client,
            metadata_base_url,
            cache: RwLock::new(None),
        })
    }

    /// Return the shared HTTP client for IAM signBlob calls.
    ///
    /// Both metadata token fetches and signBlob POST calls reuse this single
    /// client (same 2 s / 5 s timeouts, same connection pool — AC-6).
    pub(crate) fn client(&self) -> &reqwest::Client {
        &self.client
    }

    /// Return a valid bearer token, refreshing the cache if necessary.
    ///
    /// Returns `(token, from_cache)` where `from_cache` is `true` if the
    /// returned token was served from the cache (i.e. was already present and
    /// still fresh). A `false` value means the token was just fetched — a
    /// subsequent `401` from signBlob is a hard auth failure, not a stale-cache
    /// issue, and should NOT trigger an invalidate+retry cycle.
    ///
    /// First call = fetch lazy. Subsequent calls reuse the cache until the
    /// token enters the refresh-ahead window (`now >= expires_at - 60 s`).
    ///
    /// # Errors
    /// Returns `TokenError` on fetch failure.
    pub async fn get_or_refresh(&self) -> Result<(SecretString, bool), TokenError> {
        // Fast path: read-lock only.
        {
            let guard = self.cache.read().await;
            if let Some(cached) = guard.as_ref() {
                if Utc::now() < cached.expires_at - Duration::seconds(REFRESH_AHEAD_SECS) {
                    return Ok((cached.bearer.clone(), true));
                }
            }
        }
        // Slow path: write-lock + double-check.
        let mut guard = self.cache.write().await;
        if let Some(cached) = guard.as_ref() {
            if Utc::now() < cached.expires_at - Duration::seconds(REFRESH_AHEAD_SECS) {
                return Ok((cached.bearer.clone(), true));
            }
        }
        let fresh = self.fetch_token().await?;
        let bearer = fresh.bearer.clone();
        *guard = Some(fresh);
        Ok((bearer, false))
    }

    /// Evict the cached token so the next `get_or_refresh` forces a fresh fetch.
    ///
    /// Called by `sign_get` after receiving a `401` from `signBlob` when the
    /// token WAS served from cache (AC-3 retry-on-401).
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
