//! Cloud Run metadata-server ID-token provider for service-to-service auth.
//!
//! `IdTokenProvider` fetches a Google-signed ID token (JWT) from the Cloud Run
//! metadata identity endpoint and caches it with refresh-ahead semantics.
//!
//! This is a sibling of `TokenProvider` (OAuth access tokens).  The two are
//! intentionally NOT merged — an ID-token authenticates the caller to Cloud Run
//! IAM, while an OAuth access token scopes the caller to GCS / Cloud SQL APIs.
//! They carry different audiences and have different semantic lifetimes.
//!
//! AC-1: `TokenProvider` remains strictly untouched.
//! AC-2: Fetches `GET http://metadata.google.internal/.../identity?audience=<url>`.
//! AC-3: Parses `exp` from JWT payload; falls back to `now+55min` on any error.
//! AC-4: Refresh-ahead at `REFRESH_AHEAD_SECS = 60` using the same `RwLock` pattern.

use base64::Engine as _;
use chrono::{DateTime, Duration, Utc};
use secrecy::SecretString;
use tokio::sync::RwLock;

use crate::auth_metadata::token::{
    classify_reqwest_error, TokenError, CONNECT_TIMEOUT_SECS, TOTAL_TIMEOUT_SECS,
};

/// Path to the Cloud Run metadata identity endpoint.
const METADATA_IDENTITY_PATH: &str =
    "/computeMetadata/v1/instance/service-accounts/default/identity";

/// Seconds before expiry at which the ID-token is considered stale (AC-4).
///
/// Mirrors `REFRESH_AHEAD_SECS` in `TokenProvider`.  60 s gives enough buffer
/// for a slow refresh without wasting token lifetime.
const REFRESH_AHEAD_SECS: i64 = 60;

/// Fallback lifetime used when JWT `exp` cannot be parsed (AC-3).
///
/// 55 minutes is chosen to stay strictly below the 60-minute Google default
/// and the 60-second refresh-ahead window, so a fallback token is still
/// refreshed before expiry in the worst case.
const FALLBACK_LIFETIME_MINUTES: i64 = 55;

/// An active cached ID token (JWT) with its parsed expiry.
struct CachedIdToken {
    /// The raw JWT string, stored as a secret (never logged).
    bearer: SecretString,
    /// Resolved token expiry derived from the JWT `exp` claim, or a
    /// conservative fallback if parsing failed (AC-3).
    expires_at: DateTime<Utc>,
}

/// Thread-safe Google-signed ID-token provider with refresh-ahead cache.
///
/// Call `fetch_id_token` to obtain a bearer string ready for use in an
/// `Authorization: Bearer` header.  The cache is per-`IdTokenProvider`
/// instance; each audience should have its own instance (AC-1 / non-goals).
pub struct IdTokenProvider {
    client: reqwest::Client,
    metadata_base_url: String,
    audience: String,
    cache: RwLock<Option<CachedIdToken>>,
}

impl IdTokenProvider {
    /// Build an `IdTokenProvider` pointing at the real Cloud Run metadata server.
    ///
    /// `audience` must be the exact Cloud Run service URL (no trailing slash or
    /// path suffix) expected by Cloud Run IAM (AC-2 / OQ-2).
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP client cannot be constructed.
    pub fn with_audience(audience: String) -> Result<Self, TokenError> {
        Self::with_base_url_and_audience("http://metadata.google.internal".to_string(), audience)
    }

    /// Build an `IdTokenProvider` with a pre-seeded stub token that never contacts the
    /// metadata server.  Suitable for integration tests that exercise the chat path but
    /// do not need to test the ID-token fetch itself (those use `with_base_url_and_audience`
    /// + a mockito metadata server instead).
    ///
    /// The stub bearer is a fixed sentinel value; tests that assert on the actual bearer
    /// value or on the metadata server call count should NOT use this ctor.
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP client cannot be constructed.
    #[cfg(any(test, feature = "test-utils"))]
    pub fn new_stub_always_valid() -> Result<Self, TokenError> {
        let client = reqwest::Client::builder()
            .connect_timeout(std::time::Duration::from_secs(CONNECT_TIMEOUT_SECS))
            .timeout(std::time::Duration::from_secs(TOTAL_TIMEOUT_SECS))
            .build()
            .map_err(|_| TokenError::Network)?;

        let stub_token = CachedIdToken {
            bearer: SecretString::from("stub-id-token-for-tests".to_string()),
            // Expire far in the future so the cache is always fresh.
            expires_at: Utc::now() + Duration::hours(24),
        };

        Ok(Self {
            client,
            metadata_base_url: "http://metadata.google.internal".to_string(),
            audience: "http://stub-audience".to_string(),
            cache: RwLock::new(Some(stub_token)),
        })
    }

    /// Build an `IdTokenProvider` with an injectable metadata base URL (test ctor).
    ///
    /// # Errors
    ///
    /// Returns an error if the HTTP client cannot be constructed.
    pub fn with_base_url_and_audience(
        metadata_base_url: String,
        audience: String,
    ) -> Result<Self, TokenError> {
        let client = reqwest::Client::builder()
            .connect_timeout(std::time::Duration::from_secs(CONNECT_TIMEOUT_SECS))
            .timeout(std::time::Duration::from_secs(TOTAL_TIMEOUT_SECS))
            .build()
            .map_err(|_| TokenError::Network)?;

        Ok(Self {
            client,
            metadata_base_url,
            audience,
            cache: RwLock::new(None),
        })
    }

    /// Return a valid ID-token bearer string, refreshing the cache if necessary.
    ///
    /// Implements the same read-lock fast-path / write-lock double-check pattern
    /// as `TokenProvider::get_or_refresh` (AC-4).  First call is lazy; subsequent
    /// calls serve from cache until the token enters the 60-second refresh-ahead
    /// window.
    ///
    /// # Errors
    ///
    /// Returns `TokenError` if the metadata server fetch fails.
    pub async fn fetch_id_token(&self) -> Result<SecretString, TokenError> {
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
        let fresh = self.fetch_from_metadata().await?;
        let bearer = fresh.bearer.clone();
        *guard = Some(fresh);
        Ok(bearer)
    }

    /// Fetch a fresh ID-token from the metadata identity endpoint.
    async fn fetch_from_metadata(&self) -> Result<CachedIdToken, TokenError> {
        let url = format!(
            "{}{}?audience={}",
            self.metadata_base_url,
            METADATA_IDENTITY_PATH,
            percent_encode_audience(&self.audience),
        );

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

        let jwt = resp.text().await.map_err(|_| TokenError::Fetch)?;
        let expires_at = parse_jwt_exp(&jwt);

        Ok(CachedIdToken {
            bearer: SecretString::from(jwt),
            expires_at,
        })
    }
}

/// Parse the `exp` claim from a JWT payload segment (base64url, no signature verify).
///
/// The gateway trusts the metadata server HTTPS connection, not the JWT signature
/// (spec non-goals).  We only read `exp` to set the cache TTL.
///
/// On any parse failure, emits `event="id_token.exp_parse_failed"` at `warn` level
/// and returns `now + 55 min` (AC-3).  The JWT bearer itself is never logged.
fn parse_jwt_exp(jwt: &str) -> DateTime<Utc> {
    // AC-3: missing_segment — a JWT must have at least two dot-separated segments.
    let Some(payload_b64) = jwt.split('.').nth(1) else {
        tracing::warn!(
            event = "id_token.exp_parse_failed",
            reason = "missing_segment"
        );
        return fallback_expires_at();
    };

    let exp_secs = match decode_exp_from_payload(payload_b64) {
        Ok(n) => n,
        Err(reason) => {
            tracing::warn!(event = "id_token.exp_parse_failed", reason);
            return fallback_expires_at();
        }
    };

    DateTime::from_timestamp(exp_secs, 0).unwrap_or_else(fallback_expires_at)
}

/// Decode the base64url payload segment and extract the `exp` claim.
///
/// Returns the epoch-second value on success, or a static `reason` string on
/// any failure (consumed by `parse_jwt_exp` for the warn log).
fn decode_exp_from_payload(payload_b64: &str) -> Result<i64, &'static str> {
    // AC-3: b64_decode
    let payload_bytes = base64::engine::general_purpose::URL_SAFE_NO_PAD
        .decode(payload_b64)
        .map_err(|_| "b64_decode")?;

    // AC-3: json_decode
    let payload: serde_json::Value =
        serde_json::from_slice(&payload_bytes).map_err(|_| "json_decode")?;

    // AC-3: missing_exp
    let exp_value = payload.get("exp").ok_or("missing_exp")?;

    // AC-3: exp_not_numeric
    exp_value.as_i64().ok_or("exp_not_numeric")
}

/// Conservative fallback expiry when JWT `exp` cannot be parsed (AC-3).
fn fallback_expires_at() -> DateTime<Utc> {
    Utc::now() + Duration::minutes(FALLBACK_LIFETIME_MINUTES)
}

/// Percent-encode an audience URL for use as a query-string value.
///
/// Encodes all characters that are not unreserved per RFC 3986 §2.3.
/// This covers the `/`, `:`, `.` characters that appear in HTTPS service URLs
/// (e.g. `https://archiviste-workers-xxx.run.app`).
fn percent_encode_audience(audience: &str) -> String {
    let mut out = String::with_capacity(audience.len() * 3);
    for b in audience.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char);
            }
            _ => {
                out.push('%');
                out.push(hex_nibble(b >> 4));
                out.push(hex_nibble(b & 0xF));
            }
        }
    }
    out
}

/// Map a nibble value (0..=15) to its uppercase hex ASCII character.
const fn hex_nibble(n: u8) -> char {
    b"0123456789ABCDEF"[n as usize] as char
}

// ---------------------------------------------------------------------------
// Unit tests (AC-2, AC-3, AC-4)
// ---------------------------------------------------------------------------

#[cfg(test)]
mod tests {
    #![allow(clippy::expect_used, clippy::unwrap_used)]

    use super::*;
    use chrono::Utc;
    use secrecy::ExposeSecret as _;

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    /// Build a minimal JWT string with the given `exp` (epoch seconds).
    ///
    /// Header and signature are stubs — only the payload matters for `parse_jwt_exp`.
    fn make_jwt_stub(exp: i64) -> String {
        let header = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .encode(r#"{"alg":"RS256","typ":"JWT"}"#);
        let payload_json = format!(r#"{{"sub":"default","exp":{exp}}}"#);
        let payload = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(&payload_json);
        format!("{header}.{payload}.stub-sig")
    }

    // -----------------------------------------------------------------------
    // AC-2 : fetch_id_token calls the identity endpoint with percent-encoded audience
    // -----------------------------------------------------------------------

    /// AC-2: `fetch_id_token` hits `/computeMetadata/v1/instance/service-accounts/default/identity`
    /// with the audience percent-encoded as a query parameter.
    #[tokio::test]
    async fn fetch_id_token_calls_identity_endpoint() {
        let exp = Utc::now().timestamp() + 3600;
        let jwt = make_jwt_stub(exp);

        let mut server = mockito::Server::new_async().await;
        let mock = server
            .mock(
                "GET",
                "/computeMetadata/v1/instance/service-accounts/default/identity?audience=http%3A%2F%2Ftest-workers",
            )
            .match_header("Metadata-Flavor", "Google")
            .with_status(200)
            .with_header("content-type", "text/plain")
            .with_body(jwt.clone())
            .expect(1)
            .create_async()
            .await;

        let provider = IdTokenProvider::with_base_url_and_audience(
            server.url(),
            "http://test-workers".to_string(),
        )
        .expect("build provider");
        let token = provider.fetch_id_token().await.expect("fetch_id_token");

        assert_eq!(token.expose_secret(), &jwt);
        mock.assert_async().await;
    }

    // -----------------------------------------------------------------------
    // AC-3 : JWT exp parsing — 5-case matrix
    // -----------------------------------------------------------------------

    /// AC-3(a): valid JWT with `exp=now+3600` → `expires_at` ≈ now+3600 within ±2s.
    #[tokio::test]
    async fn exp_parsed_from_valid_jwt() {
        let exp = Utc::now().timestamp() + 3600;
        let jwt = make_jwt_stub(exp);
        let result = parse_jwt_exp(&jwt);
        let delta = (result.timestamp() - exp).abs();
        assert!(delta <= 2, "expires_at delta too large: {delta}s");
    }

    /// AC-3(b): bearer with no dot separator → fallback `now+55min` + log `reason=missing_segment`.
    #[tokio::test]
    #[tracing_test::traced_test]
    async fn exp_fallback_on_missing_segment() {
        let before = Utc::now();
        let result = parse_jwt_exp("nodots");
        let after = Utc::now();

        // Fallback is now+55min; check it is in [before+55min, after+55min].
        let low = before + Duration::minutes(FALLBACK_LIFETIME_MINUTES);
        let high = after + Duration::minutes(FALLBACK_LIFETIME_MINUTES);
        assert!(
            result >= low && result <= high,
            "fallback outside expected window"
        );

        assert!(logs_contain("id_token.exp_parse_failed"));
        assert!(logs_contain("missing_segment"));
    }

    /// AC-3(c): payload segment contains character `!` (not valid base64url) →
    /// fallback + log `reason=b64_decode`.
    #[tokio::test]
    #[tracing_test::traced_test]
    async fn exp_fallback_on_invalid_b64() {
        let before = Utc::now();
        // `!` is not in the base64url alphabet; decoding will fail.
        let jwt = "header.!!invalid!!.sig";
        let result = parse_jwt_exp(jwt);
        let after = Utc::now();

        let low = before + Duration::minutes(FALLBACK_LIFETIME_MINUTES);
        let high = after + Duration::minutes(FALLBACK_LIFETIME_MINUTES);
        assert!(result >= low && result <= high);

        assert!(logs_contain("id_token.exp_parse_failed"));
        assert!(logs_contain("b64_decode"));
    }

    /// AC-3(d): payload base64-decodes but is not valid JSON → fallback + log `reason=json_decode`.
    #[tokio::test]
    #[tracing_test::traced_test]
    async fn exp_fallback_on_invalid_json() {
        let before = Utc::now();
        // Encode bytes that are not valid UTF-8 JSON.
        let bad_payload =
            base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(b"\xff\xfe not json");
        let jwt = format!("header.{bad_payload}.sig");
        let result = parse_jwt_exp(&jwt);
        let after = Utc::now();

        let low = before + Duration::minutes(FALLBACK_LIFETIME_MINUTES);
        let high = after + Duration::minutes(FALLBACK_LIFETIME_MINUTES);
        assert!(result >= low && result <= high);

        assert!(logs_contain("id_token.exp_parse_failed"));
        assert!(logs_contain("json_decode"));
    }

    /// AC-3(e): valid JSON but `exp` key absent → fallback + log `reason=missing_exp`.
    #[tokio::test]
    #[tracing_test::traced_test]
    async fn exp_fallback_on_missing_exp_claim() {
        let before = Utc::now();
        let payload = base64::engine::general_purpose::URL_SAFE_NO_PAD
            .encode(r#"{"sub":"default","iat":1000}"#);
        let jwt = format!("header.{payload}.sig");
        let result = parse_jwt_exp(&jwt);
        let after = Utc::now();

        let low = before + Duration::minutes(FALLBACK_LIFETIME_MINUTES);
        let high = after + Duration::minutes(FALLBACK_LIFETIME_MINUTES);
        assert!(result >= low && result <= high);

        assert!(logs_contain("id_token.exp_parse_failed"));
        assert!(logs_contain("missing_exp"));
    }

    // -----------------------------------------------------------------------
    // AC-4 : cache semantics
    // -----------------------------------------------------------------------

    /// AC-4 cache-hit: two `fetch_id_token` calls → only one metadata server hit.
    #[tokio::test]
    async fn cache_hit_skips_metadata_fetch() {
        let exp = Utc::now().timestamp() + 3600;
        let jwt = make_jwt_stub(exp);

        let mut server = mockito::Server::new_async().await;
        let mock = server
            .mock(
                "GET",
                "/computeMetadata/v1/instance/service-accounts/default/identity?audience=http%3A%2F%2Ftest-workers",
            )
            .with_status(200)
            .with_body(jwt.clone())
            .expect(1) // exactly one fetch
            .create_async()
            .await;

        let provider = IdTokenProvider::with_base_url_and_audience(
            server.url(),
            "http://test-workers".to_string(),
        )
        .expect("build provider");

        let t1 = provider.fetch_id_token().await.expect("first fetch");
        let t2 = provider
            .fetch_id_token()
            .await
            .expect("second fetch (cache)");

        assert_eq!(t1.expose_secret(), &jwt);
        assert_eq!(t2.expose_secret(), &jwt);
        mock.assert_async().await;
    }

    /// AC-4 refresh-ahead: token with `exp=now+30s` (inside 60s window) →
    /// both calls fetch from metadata (expect(2)).
    #[tokio::test]
    async fn cache_refresh_ahead_window() {
        // exp only 30 seconds away — within the 60-second refresh-ahead window,
        // so the cache is considered stale on every call.
        let exp = Utc::now().timestamp() + 30;
        let jwt = make_jwt_stub(exp);

        let mut server = mockito::Server::new_async().await;
        let mock = server
            .mock(
                "GET",
                "/computeMetadata/v1/instance/service-accounts/default/identity?audience=http%3A%2F%2Ftest-workers",
            )
            .with_status(200)
            .with_body(jwt.clone())
            .expect(2) // both calls must hit the server
            .create_async()
            .await;

        let provider = IdTokenProvider::with_base_url_and_audience(
            server.url(),
            "http://test-workers".to_string(),
        )
        .expect("build provider");

        let _t1 = provider.fetch_id_token().await.expect("first fetch");
        let _t2 = provider
            .fetch_id_token()
            .await
            .expect("second fetch (stale)");
        mock.assert_async().await;
    }
}
