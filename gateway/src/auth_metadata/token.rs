//! Cloud Run metadata-server bearer token provider for IAM-scoped OAuth tokens.
//!
//! `TokenProvider` fetches an OAuth access token from the Cloud Run metadata
//! server and caches it with refresh-ahead semantics.
//!
//! Two named constructors expose the two scope variants used in this project:
//! - `TokenProvider::for_gcs_signing()` — default SA scope (SEC-004 signBlob).
//! - `TokenProvider::for_cloud_sql()` — `sqlservice.admin` scope (SEC-005 Cloud SQL IAM).
//!
//! The metadata base URL is injectable for testing; production uses the
//! standard Google Cloud endpoint.  Each instance holds its own independent
//! cache (`AC-2`: no shared cache between GCS and SQL token providers).

use chrono::{DateTime, Duration, Utc};
use secrecy::SecretString;
use thiserror::Error;
use tokio::sync::RwLock;

/// TCP connect timeout for the metadata/signing HTTP client.
pub(crate) const CONNECT_TIMEOUT_SECS: u64 = 2;

/// Total request timeout for the metadata/signing HTTP client.
pub(crate) const TOTAL_TIMEOUT_SECS: u64 = 5;

/// Path to the default service-account token on the GCE/Cloud Run metadata server.
const METADATA_TOKEN_PATH: &str = "/computeMetadata/v1/instance/service-accounts/default/token";

/// Seconds before expiry at which the token is considered stale and refreshed proactively.
const REFRESH_AHEAD_SECS: i64 = 60;

/// OAuth 2.0 scope newtype — makes the two scope variants unrepresentable as each other (AC-2).
///
/// Use the named constants `OAuthScope::GCS_DEFAULT` and `OAuthScope::CLOUD_SQL`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct OAuthScope(&'static str);

impl OAuthScope {
    /// Default service-account scope: no `scopes=` query param appended (GCS signBlob, SEC-004).
    pub const GCS_DEFAULT: Self = Self("");

    /// Cloud SQL IAM auth scope (`sqlservice.admin`, SEC-005).
    pub const CLOUD_SQL: Self = Self("https://www.googleapis.com/auth/sqlservice.admin");

    /// Return `Some(("scopes", value))` when a non-empty scope should be appended to the URL.
    pub(crate) fn as_query_pair(self) -> Option<(&'static str, &'static str)> {
        if self.0.is_empty() {
            None
        } else {
            Some(("scopes", self.0))
        }
    }
}

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

/// Thread-safe bearer token provider with refresh-ahead cache.
///
/// Holds a dedicated `reqwest::Client` with 2 s connect / 5 s total timeouts.
/// The same client is reused for both metadata token fetches and signBlob POST
/// calls via `TokenProvider::client()`.  The metadata base URL is injectable
/// for testing.  Each instance is scope-specific and holds its own cache (AC-2).
pub struct TokenProvider {
    client: reqwest::Client,
    metadata_base_url: String,
    scope: OAuthScope,
    cache: RwLock<Option<CachedToken>>,
}

impl TokenProvider {
    /// Build a `TokenProvider` for GCS signBlob (default SA scope, SEC-004 backward-compat).
    ///
    /// # Errors
    /// Returns an error if the HTTP client cannot be constructed.
    pub fn for_gcs_signing() -> Result<Self, TokenError> {
        Self::with_base_url(
            "http://metadata.google.internal".to_string(),
            OAuthScope::GCS_DEFAULT,
        )
    }

    /// Build a `TokenProvider` for Cloud SQL IAM auth (`sqlservice.admin` scope, SEC-005).
    ///
    /// # Errors
    /// Returns an error if the HTTP client cannot be constructed.
    pub fn for_cloud_sql() -> Result<Self, TokenError> {
        Self::with_base_url(
            "http://metadata.google.internal".to_string(),
            OAuthScope::CLOUD_SQL,
        )
    }

    /// Build a `TokenProvider` with an injectable metadata base URL and explicit scope (test ctor).
    ///
    /// # Errors
    /// Returns an error if the HTTP client cannot be constructed.
    pub fn with_base_url(metadata_base_url: String, scope: OAuthScope) -> Result<Self, TokenError> {
        let client = reqwest::Client::builder()
            .connect_timeout(std::time::Duration::from_secs(CONNECT_TIMEOUT_SECS))
            .timeout(std::time::Duration::from_secs(TOTAL_TIMEOUT_SECS))
            .build()
            .map_err(|_| TokenError::Network)?;

        Ok(Self {
            client,
            metadata_base_url,
            scope,
            cache: RwLock::new(None),
        })
    }

    /// Return the shared HTTP client for IAM signBlob calls.
    ///
    /// Both metadata token fetches and signBlob POST calls reuse this single
    /// client (same 2 s / 5 s timeouts, same connection pool).
    pub(crate) fn client(&self) -> &reqwest::Client {
        &self.client
    }

    /// Return a valid bearer token, refreshing the cache if necessary.
    ///
    /// Returns `(token, from_cache)` where `from_cache` is `true` if the
    /// returned token was served from the cache.  A `false` value means the
    /// token was just fetched.
    ///
    /// First call = fetch lazy.  Subsequent calls reuse the cache until the
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
    /// token WAS served from cache (SEC-004 AC-3 retry-on-401).
    pub async fn invalidate(&self) {
        *self.cache.write().await = None;
    }

    /// Fetch a fresh token from the metadata server.
    async fn fetch_token(&self) -> Result<CachedToken, TokenError> {
        let mut url = format!("{}{}", self.metadata_base_url, METADATA_TOKEN_PATH);
        if let Some((key, value)) = self.scope.as_query_pair() {
            url = format!("{url}?{key}={value}");
        }

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

// ---------------------------------------------------------------------------
// Unit tests (AC-2 oracle: scope query string)
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::unwrap_used)]

    use secrecy::ExposeSecret;

    use super::*;

    /// AC-2: `for_cloud_sql()` appends `?scopes=https%3A%2F%2F...sqlservice.admin`
    /// to the metadata URL.  Verified via a mockito server that asserts the query param.
    #[tokio::test]
    async fn for_cloud_sql_uses_sqlservice_admin_scope() {
        let mut server = mockito::Server::new_async().await;
        let mock = server
            .mock(
                "GET",
                "/computeMetadata/v1/instance/service-accounts/default/token?scopes=https://www.googleapis.com/auth/sqlservice.admin",
            )
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"access_token":"sql-token","expires_in":3600,"token_type":"Bearer"}"#)
            .expect(1)
            .create_async()
            .await;

        let provider = TokenProvider::with_base_url(server.url(), OAuthScope::CLOUD_SQL)
            .expect("build provider");
        let (token, from_cache) = provider.get_or_refresh().await.expect("get_or_refresh");

        assert_eq!(token.expose_secret(), "sql-token");
        assert!(!from_cache, "first call must not be from cache");
        mock.assert_async().await;
    }

    /// AC-2: `for_gcs_signing()` sends NO `scopes=` query param (default SA scope).
    #[tokio::test]
    async fn for_gcs_signing_uses_default_scope() {
        let mut server = mockito::Server::new_async().await;
        let mock = server
            .mock(
                "GET",
                "/computeMetadata/v1/instance/service-accounts/default/token",
            )
            .with_status(200)
            .with_header("content-type", "application/json")
            .with_body(r#"{"access_token":"gcs-token","expires_in":3600,"token_type":"Bearer"}"#)
            .expect(1)
            .create_async()
            .await;

        let provider = TokenProvider::with_base_url(server.url(), OAuthScope::GCS_DEFAULT)
            .expect("build provider");
        let (token, _) = provider.get_or_refresh().await.expect("get_or_refresh");

        assert_eq!(token.expose_secret(), "gcs-token");
        mock.assert_async().await;
    }
}
