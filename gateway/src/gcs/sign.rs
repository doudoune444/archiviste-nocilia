//! GCS V4 signed URL generation via IAM `signBlob` HTTP API (GOOG4-RSA-SHA256).
//!
//! Replaces the offline RSA-PKCS1-SHA256 path (UI-002) with a call to
//! `iamcredentials.googleapis.com`. The gateway no longer holds the SA private
//! key (AC-1, SEC-004).
//!
//! `sign_get` accepts an injectable `now: DateTime<Utc>` for deterministic tests.

use chrono::{DateTime, Utc};
use secrecy::ExposeSecret;
use thiserror::Error;

use crate::gcs::token::{classify_reqwest_error, TokenError, TokenProvider};

/// TCP connect timeout for the dedicated signing HTTP client (AC-6).
pub const CONNECT_TIMEOUT_SECS: u64 = 2;
/// Total request timeout for the dedicated signing HTTP client (AC-6).
pub const TOTAL_TIMEOUT_SECS: u64 = 5;

/// Base URL of the IAM credentials API (injectable in tests via `TokenProvider` URL args).
const IAM_BASE_URL_DEFAULT: &str = "https://iamcredentials.googleapis.com";

/// TTL (seconds) of every signed URL produced by this module (AC-9, AC-21).
pub const SIGNED_URL_TTL_SECONDS: u64 = 300;

/// Opaque signed URL value (HTTPS string).
pub type SignedUrl = String;

/// Errors from `sign_get`.
///
/// Each variant maps 1-to-1 to a `reason_code` string logged at `warn` level (AC-5).
#[derive(Debug, Error)]
pub enum SignError {
    /// Metadata token fetch failed.
    #[error("metadata_token_failed")]
    MetadataTokenFailed,
    /// `signBlob` returned 403 Forbidden (binding missing or revoked).
    #[error("signblob_403")]
    SignBlob403,
    /// `signBlob` returned 429 Too Many Requests.
    #[error("signblob_429")]
    SignBlob429,
    /// `signBlob` returned a 5xx error.
    #[error("signblob_5xx")]
    SignBlob5xx,
    /// Request to metadata server or `signBlob` timed out.
    #[error("timeout")]
    Timeout,
    /// Network-level failure (DNS, TCP, TLS).
    #[error("network")]
    Network,
}

impl SignError {
    /// Return the `reason_code` string for structured logging (AC-5).
    #[must_use]
    pub fn reason_code(&self) -> &'static str {
        match self {
            Self::MetadataTokenFailed => "metadata_token_failed",
            Self::SignBlob403 => "signblob_403",
            Self::SignBlob429 => "signblob_429",
            Self::SignBlob5xx => "signblob_5xx",
            Self::Timeout => "timeout",
            Self::Network => "network",
        }
    }
}

impl From<TokenError> for SignError {
    fn from(e: TokenError) -> Self {
        match e {
            TokenError::Timeout => Self::Timeout,
            TokenError::Network => Self::Network,
            TokenError::Fetch => Self::MetadataTokenFailed,
        }
    }
}

/// JSON body sent to `signBlob`.
#[derive(serde::Serialize)]
struct SignBlobRequest {
    payload: String,
}

/// JSON response from `signBlob`.
#[derive(serde::Deserialize)]
struct SignBlobResponse {
    #[serde(rename = "signedBlob")]
    signed_blob: String,
}

/// Dedicated HTTP client for IAM signBlob calls (2 s / 5 s — AC-6).
fn build_sign_client() -> Result<reqwest::Client, SignError> {
    reqwest::Client::builder()
        .connect_timeout(std::time::Duration::from_secs(CONNECT_TIMEOUT_SECS))
        .timeout(std::time::Duration::from_secs(TOTAL_TIMEOUT_SECS))
        .build()
        .map_err(|_| SignError::Network)
}

/// Generate a GCS V4 signed GET URL via IAM `signBlob` (AC-2).
///
/// Calls the metadata server to obtain a bearer token (AC-3), then calls
/// `POST iamcredentials.googleapis.com/v1/projects/-/serviceAccounts/{sa_email}:signBlob`.
/// On a `401` response, invalidates the token cache and retries once (AC-3).
///
/// The `iam_base_url` parameter allows injecting a mock server URL in tests.
/// Production callers should pass `None` (uses `IAM_BASE_URL_DEFAULT`).
///
/// # Errors
/// Returns `SignError` on any token fetch, network, or signBlob failure.
pub async fn sign_get(
    token_provider: &TokenProvider,
    sa_email: &str,
    bucket: &str,
    object: &str,
    now: DateTime<Utc>,
    iam_base_url: Option<&str>,
) -> Result<SignedUrl, SignError> {
    let client = build_sign_client()?;
    let iam_base = iam_base_url.unwrap_or(IAM_BASE_URL_DEFAULT);
    let iam_url = format!("{iam_base}/v1/projects/-/serviceAccounts/{sa_email}:signBlob");

    let (qs, sts) = build_string_to_sign(sa_email, bucket, object, now);

    let payload_b64 =
        base64::engine::Engine::encode(&base64::engine::general_purpose::STANDARD, sts.as_bytes());

    let sig_hex = call_sign_blob(&client, token_provider, &iam_url, &payload_b64).await?;

    let host = format!("{bucket}.storage.googleapis.com");
    Ok(format!(
        "https://{host}/{object}?{qs}&X-Goog-Signature={sig_hex}"
    ))
}

/// Build the V4 canonical query string and string-to-sign, returning both.
fn build_string_to_sign(
    sa_email: &str,
    bucket: &str,
    object: &str,
    now: DateTime<Utc>,
) -> (String, String) {
    let datetime = now.format("%Y%m%dT%H%M%SZ").to_string();
    let date = now.format("%Y%m%d").to_string();
    let scope = format!("{date}/auto/storage/goog4_request");
    let cred_encoded = format!("{sa_email}/{scope}")
        .replace('/', "%2F")
        .replace('@', "%40");
    let host = format!("{bucket}.storage.googleapis.com");
    let qs = format!(
        "X-Goog-Algorithm=GOOG4-RSA-SHA256\
         &X-Goog-Credential={cred_encoded}\
         &X-Goog-Date={datetime}\
         &X-Goog-Expires={SIGNED_URL_TTL_SECONDS}\
         &X-Goog-SignedHeaders=host"
    );
    let canonical = format!("GET\n/{object}\n{qs}\nhost:{host}\n\nhost\nUNSIGNED-PAYLOAD");
    let cr_hash = sha256_hex(canonical.as_bytes());
    let sts = format!("GOOG4-RSA-SHA256\n{datetime}\n{scope}\n{cr_hash}");
    (qs, sts)
}

/// Call the IAM `signBlob` endpoint; retry once on 401 after cache invalidation.
async fn call_sign_blob(
    client: &reqwest::Client,
    token_provider: &TokenProvider,
    iam_url: &str,
    payload_b64: &str,
) -> Result<String, SignError> {
    let token = token_provider.get_or_refresh().await?;
    let body = SignBlobRequest {
        payload: payload_b64.to_string(),
    };

    let resp = client
        .post(iam_url)
        .bearer_auth(token.expose_secret())
        .json(&body)
        .send()
        .await
        .map_err(|e| SignError::from(classify_reqwest_error(&e)))?;

    let status = resp.status();
    if status.as_u16() == 401 {
        // Retry once after invalidating the stale cached token (AC-3).
        token_provider.invalidate().await;
        let fresh_token = token_provider.get_or_refresh().await?;
        let retry_resp = client
            .post(iam_url)
            .bearer_auth(fresh_token.expose_secret())
            .json(&body)
            .send()
            .await
            .map_err(|e| SignError::from(classify_reqwest_error(&e)))?;
        return parse_sign_blob_response(retry_resp).await;
    }

    parse_sign_blob_response(resp).await
}

/// Parse a `signBlob` HTTP response into a hex-encoded signature or a `SignError`.
async fn parse_sign_blob_response(resp: reqwest::Response) -> Result<String, SignError> {
    let status = resp.status().as_u16();
    match status {
        200 => {
            let body: SignBlobResponse = resp.json().await.map_err(|_| SignError::SignBlob5xx)?;
            let sig_bytes = base64::engine::Engine::decode(
                &base64::engine::general_purpose::STANDARD,
                &body.signed_blob,
            )
            .map_err(|_| SignError::SignBlob5xx)?;
            Ok(hex::encode(sig_bytes))
        }
        403 => Err(SignError::SignBlob403),
        429 => Err(SignError::SignBlob429),
        _ => Err(SignError::SignBlob5xx),
    }
}

fn sha256_hex(data: &[u8]) -> String {
    use sha2::{Digest, Sha256};
    hex::encode(Sha256::digest(data))
}
