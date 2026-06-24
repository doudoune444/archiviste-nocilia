//! GCS object-deletion seam used by `DELETE /v1/conversations/{id}` (#283).
//!
//! Abstracts the GCS JSON-API object delete behind a small trait so the full DB
//! path (204/404/409) is testable at the HTTP level without a real bucket. The
//! production implementation calls `DELETE storage.googleapis.com/.../o/{object}`
//! with an IAM OAuth bearer token; tests inject a fake that records whether a
//! delete was issued.

use std::sync::Arc;

use secrecy::ExposeSecret;
use thiserror::Error;

use crate::auth_metadata::token::{classify_reqwest_error, TokenError, TokenProvider};

/// Base URL of the GCS JSON API (injectable in tests via the deleter constructor).
const GCS_JSON_API_BASE_URL: &str = "https://storage.googleapis.com";

/// Failure deleting a GCS object — the conversation delete must roll back on any of these.
#[derive(Debug, Error)]
pub enum GcsDeleteError {
    /// Metadata token fetch failed.
    #[error("metadata_token_failed")]
    MetadataTokenFailed,
    /// GCS returned a non-success, non-404 status (403, 5xx, …).
    #[error("gcs_status_{0}")]
    Status(u16),
    /// Request timed out.
    #[error("timeout")]
    Timeout,
    /// Network-level failure (DNS, TCP, TLS).
    #[error("network")]
    Network,
}

impl From<TokenError> for GcsDeleteError {
    fn from(error: TokenError) -> Self {
        match error {
            TokenError::Timeout => Self::Timeout,
            TokenError::Network => Self::Network,
            TokenError::Fetch => Self::MetadataTokenFailed,
        }
    }
}

/// Deletes one object from the conversation-transcript GCS bucket.
///
/// A successful return (including the object being already absent) means the
/// transcript is gone; any `Err` rolls back the Postgres delete (#283 atomicity).
pub trait GcsObjectDeleter: Send + Sync {
    /// Delete the object at `object_path` (the bucket-relative path, no `gs://` prefix).
    fn delete_object<'a>(
        &'a self,
        object_path: &'a str,
    ) -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<(), GcsDeleteError>> + Send + 'a>>;
}

/// Production `GcsObjectDeleter` calling the GCS JSON API with an IAM OAuth token.
pub struct GcsApiObjectDeleter {
    token_provider: Arc<TokenProvider>,
    bucket: String,
    base_url: String,
}

impl GcsApiObjectDeleter {
    /// Build a deleter for `bucket`, signing requests with `token_provider`.
    #[must_use]
    pub fn new(token_provider: Arc<TokenProvider>, bucket: String) -> Self {
        Self {
            token_provider,
            bucket,
            base_url: GCS_JSON_API_BASE_URL.to_string(),
        }
    }

    /// Build a deleter pointed at an injected base URL (test ctor for a mock GCS server).
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub fn with_base_url(
        token_provider: Arc<TokenProvider>,
        bucket: String,
        base_url: String,
    ) -> Self {
        Self {
            token_provider,
            bucket,
            base_url,
        }
    }

    async fn delete(&self, object_path: &str) -> Result<(), GcsDeleteError> {
        let object_encoded = encode_object_path(object_path);
        let url = format!(
            "{}/storage/v1/b/{}/o/{}",
            self.base_url, self.bucket, object_encoded
        );
        let (token, _) = self.token_provider.get_or_refresh().await?;

        let response = self
            .token_provider
            .client()
            .delete(&url)
            .bearer_auth(token.expose_secret())
            .send()
            .await
            .map_err(|error| GcsDeleteError::from(classify_reqwest_error(&error)))?;

        classify_delete_status(response.status().as_u16())
    }
}

/// Map a GCS delete HTTP status to success or a typed error.
///
/// `204`/`200` is success; `404` is also success — the transcript is already
/// gone, which satisfies the delete intent (idempotent). Anything else is an error.
fn classify_delete_status(status: u16) -> Result<(), GcsDeleteError> {
    match status {
        200 | 204 | 404 => Ok(()),
        other => Err(GcsDeleteError::Status(other)),
    }
}

/// Percent-encode a bucket-relative object path for the GCS JSON-API `o/{object}` segment.
///
/// The slash separators inside the path become `%2F` (single path segment), and any
/// other reserved character is percent-encoded. Only RFC 3986 unreserved characters
/// pass through, so a user-supplied path can never escape the segment.
fn encode_object_path(object_path: &str) -> String {
    const HEX_UPPER: [u8; 16] = *b"0123456789ABCDEF";
    let mut encoded = String::with_capacity(object_path.len() * 3);
    for byte in object_path.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                encoded.push(byte as char);
            }
            _ => {
                encoded.push('%');
                encoded.push(HEX_UPPER[usize::from(byte >> 4)] as char);
                encoded.push(HEX_UPPER[usize::from(byte & 0x0F)] as char);
            }
        }
    }
    encoded
}

impl GcsObjectDeleter for GcsApiObjectDeleter {
    fn delete_object<'a>(
        &'a self,
        object_path: &'a str,
    ) -> std::pin::Pin<Box<dyn std::future::Future<Output = Result<(), GcsDeleteError>> + Send + 'a>>
    {
        Box::pin(self.delete(object_path))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn object_path_slashes_become_segment_safe() {
        assert_eq!(encode_object_path("conv/abc.md"), "conv%2Fabc.md");
    }

    #[test]
    fn absent_object_is_success() {
        assert!(classify_delete_status(404).is_ok());
        assert!(classify_delete_status(204).is_ok());
    }

    #[test]
    fn forbidden_is_error() {
        assert!(matches!(
            classify_delete_status(403),
            Err(GcsDeleteError::Status(403))
        ));
    }
}
