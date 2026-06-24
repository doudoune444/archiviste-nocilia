//! GCS object deletion seam (#283).
//!
//! Abstracts deleting a single GCS object behind [`GcsObjectDeleter`] so the
//! conversation-delete handler can be exercised at the HTTP level without a real
//! GCS backend. The real implementation [`GcsApiObjectDeleter`] calls the JSON GCS
//! API (`DELETE /storage/v1/b/{bucket}/o/{object}`) via the shared `reqwest` client
//! and the GCS IAM token provider; tests inject a recording fake.
//!
//! Implemented as an object-safe async trait using a boxed `Future` return to
//! support `dyn GcsObjectDeleter` (native `async fn in trait` is not yet dyn-safe
//! on Rust 1.95 stable), mirroring `auth::user_lookup::UserLookup`.

use secrecy::ExposeSecret;
use std::pin::Pin;
use std::sync::Arc;
use thiserror::Error;

use crate::auth_metadata::token::{classify_reqwest_error, TokenError, TokenProvider};

/// Default base URL of the JSON GCS API (injectable in tests).
const GCS_JSON_API_BASE_URL: &str = "https://storage.googleapis.com";

/// Errors from a GCS object delete.
#[derive(Debug, Error)]
pub enum GcsDeleteError {
    /// Metadata token fetch failed.
    #[error("metadata_token_failed")]
    MetadataTokenFailed,
    /// The GCS API returned a non-success status (other than 404, which is
    /// treated as already-absent and therefore success).
    #[error("gcs_api_error")]
    ApiError,
    /// Request to the GCS API timed out.
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

/// Boxed future returned by [`GcsObjectDeleter::delete`].
pub type DeleteFuture<'a> =
    Pin<Box<dyn std::future::Future<Output = Result<(), GcsDeleteError>> + Send + 'a>>;

/// Deletes a single GCS object within a bucket.
///
/// `object` is the object path inside the bucket (no `gs://bucket/` prefix).
pub trait GcsObjectDeleter: Send + Sync {
    /// Delete the object at `object` within `bucket`.
    ///
    /// A missing object (404) is treated as success — delete is idempotent.
    ///
    /// # Errors
    /// Returns [`GcsDeleteError`] on token fetch, network, or non-404 API failure.
    fn delete<'a>(&'a self, bucket: &'a str, object: &'a str) -> DeleteFuture<'a>;
}

/// Real [`GcsObjectDeleter`] backed by the JSON GCS API and the GCS IAM token provider.
pub struct GcsApiObjectDeleter {
    token_provider: Arc<TokenProvider>,
    base_url: String,
}

impl GcsApiObjectDeleter {
    /// Build a deleter using the production JSON GCS API base URL.
    #[must_use]
    pub fn new(token_provider: Arc<TokenProvider>) -> Self {
        Self {
            token_provider,
            base_url: GCS_JSON_API_BASE_URL.to_string(),
        }
    }

    /// Build a deleter pointing at an injected base URL (test mock server).
    #[cfg(any(test, feature = "test-utils"))]
    #[must_use]
    pub fn with_base_url(token_provider: Arc<TokenProvider>, base_url: String) -> Self {
        Self {
            token_provider,
            base_url,
        }
    }

    async fn delete_object(&self, bucket: &str, object: &str) -> Result<(), GcsDeleteError> {
        let (token, _) = self.token_provider.get_or_refresh().await?;
        let encoded_object = encode_object_path(object);
        let url = format!("{}/storage/v1/b/{bucket}/o/{encoded_object}", self.base_url);

        let response = self
            .token_provider
            .client()
            .delete(&url)
            .bearer_auth(token.expose_secret())
            .send()
            .await
            .map_err(|error| GcsDeleteError::from(classify_reqwest_error(&error)))?;

        let status = response.status().as_u16();
        // 404 = already absent → idempotent success; 2xx = deleted.
        if status == 404 || (200..300).contains(&status) {
            Ok(())
        } else {
            Err(GcsDeleteError::ApiError)
        }
    }
}

impl GcsObjectDeleter for GcsApiObjectDeleter {
    fn delete<'a>(&'a self, bucket: &'a str, object: &'a str) -> DeleteFuture<'a> {
        Box::pin(self.delete_object(bucket, object))
    }
}

/// Recording [`GcsObjectDeleter`] fake for tests (#283).
///
/// Records whether a delete was issued (asserted per path: issued on 204, not
/// issued on 404/409) and can be configured to simulate a GCS failure so the
/// handler's transaction-rollback path is exercised.
#[cfg(any(test, feature = "test-utils"))]
pub struct RecordingGcsDeleter {
    issued: std::sync::atomic::AtomicBool,
    should_fail: bool,
}

#[cfg(any(test, feature = "test-utils"))]
impl RecordingGcsDeleter {
    /// A fake that records deletes and reports success.
    #[must_use]
    pub fn succeeding() -> Self {
        Self {
            issued: std::sync::atomic::AtomicBool::new(false),
            should_fail: false,
        }
    }

    /// A fake that records deletes and always returns [`GcsDeleteError::ApiError`].
    #[must_use]
    pub fn failing() -> Self {
        Self {
            issued: std::sync::atomic::AtomicBool::new(false),
            should_fail: true,
        }
    }

    /// Whether a delete was issued to this fake.
    #[must_use]
    pub fn was_issued(&self) -> bool {
        self.issued.load(std::sync::atomic::Ordering::SeqCst)
    }
}

#[cfg(any(test, feature = "test-utils"))]
impl GcsObjectDeleter for RecordingGcsDeleter {
    fn delete<'a>(&'a self, _bucket: &'a str, _object: &'a str) -> DeleteFuture<'a> {
        self.issued.store(true, std::sync::atomic::Ordering::SeqCst);
        Box::pin(async move {
            if self.should_fail {
                Err(GcsDeleteError::ApiError)
            } else {
                Ok(())
            }
        })
    }
}

/// Percent-encode an object path for the JSON GCS API (slashes must be `%2F`).
fn encode_object_path(object: &str) -> String {
    use std::fmt::Write as _;
    let mut encoded = String::with_capacity(object.len());
    for byte in object.bytes() {
        match byte {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'.' | b'_' | b'~' => {
                encoded.push(byte as char);
            }
            other => {
                let _ = write!(encoded, "%{other:02X}");
            }
        }
    }
    encoded
}
