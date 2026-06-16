//! Leaf utilities shared by gateway handlers that proxy to the workers tier.
//!
//! # What lives here
//! - [`MAX_UPSTREAM_BODY`] — 256 KiB cap applied to every upstream response.
//! - [`ErrorBody`] — uniform error envelope (machine-readable code + `request_id`).
//! - [`build_error`] — builds a JSON error response from that envelope.
//! - [`build_passthrough`] — wraps raw upstream bytes into a 200 pass-through response.
//! - [`read_capped_body`] — streaming reader that enforces the 256 KiB cap.
//! - [`map_reqwest_error`] — maps a `reqwest::Error` to the correct gateway status.
//! - [`classify_id_token_error`] — maps a `TokenError` to an AC-6 reason code string.
//! - [`elapsed_ms`] — safe `Instant → i64` millisecond conversion.
//!
//! # What does NOT live here
//! Per-handler concerns stay in each handler: request validation, log fields
//! (`query_len` vs `claim_len`), workers URL, JSON body shape, and the full
//! forwarding block. Extracting those would require parameterisation that collapses
//! two distinct read-top-to-bottom handlers into an opaque mega-function.

use axum::{
    http::{HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use serde::Serialize;

use crate::auth_metadata::token::TokenError;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Maximum upstream response body size in bytes (AC-15 / A04 `DoS` guard).
pub const MAX_UPSTREAM_BODY: usize = 262_144; // 256 KiB

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/// Uniform error envelope returned on any non-200 response.
#[derive(Debug, Serialize)]
pub struct ErrorBody {
    /// Machine-readable error code.
    pub error: &'static str,
    /// Gateway-generated `UUIDv4` request identifier.
    pub request_id: String,
}

// ---------------------------------------------------------------------------
// Response builders
// ---------------------------------------------------------------------------

/// Build an error response with the uniform envelope (AC-12).
#[must_use]
pub fn build_error(request_id: &str, code: &'static str, status: StatusCode) -> Response {
    let body = ErrorBody {
        error: code,
        request_id: request_id.to_string(),
    };
    (
        status,
        [(
            axum::http::header::CONTENT_TYPE,
            HeaderValue::from_static("application/json; charset=utf-8"),
        )],
        Json(body),
    )
        .into_response()
}

/// Build a passthrough response from raw upstream bytes (R5).
pub fn build_passthrough(request_id: &str, bytes: axum::body::Bytes) -> Response {
    Response::builder()
        .status(StatusCode::OK)
        .header("content-type", "application/json; charset=utf-8")
        .header("x-request-id", request_id)
        .body(axum::body::Body::from(bytes))
        .unwrap_or_else(|_| build_error(request_id, "internal", StatusCode::INTERNAL_SERVER_ERROR))
}

// ---------------------------------------------------------------------------
// Upstream body reader
// ---------------------------------------------------------------------------

/// Read up to [`MAX_UPSTREAM_BODY`] bytes chunk-by-chunk.
///
/// Rejects immediately when the accumulator exceeds the cap — avoids buffering
/// the full upstream body in memory before checking (AC-15 / A04 `DoS` guard).
///
/// # Errors
/// Returns `Err(())` if the upstream stream yields a transport error, or if the
/// accumulated body would exceed [`MAX_UPSTREAM_BODY`].
pub async fn read_capped_body(resp: reqwest::Response) -> Result<axum::body::Bytes, ()> {
    use futures_util::StreamExt as _;

    let mut acc = bytes::BytesMut::with_capacity(MAX_UPSTREAM_BODY);
    let mut stream = resp.bytes_stream();
    while let Some(chunk) = stream.next().await {
        let chunk = chunk.map_err(|_| ())?;
        if acc.len() + chunk.len() > MAX_UPSTREAM_BODY {
            return Err(());
        }
        acc.extend_from_slice(&chunk);
    }
    Ok(acc.freeze())
}

// ---------------------------------------------------------------------------
// Error mappers
// ---------------------------------------------------------------------------

/// Map a `reqwest::Error` to the appropriate gateway error response.
///
/// Priority: connection errors (AC-9) are checked before timeout (AC-8)
/// because a connect-phase timeout sets both `is_connect()` and `is_timeout()`.
#[must_use]
pub fn map_reqwest_error(
    err: &reqwest::Error,
    request_id: &str,
) -> (StatusCode, Option<u16>, Response) {
    if err.is_connect() {
        // AC-9: connection refused, DNS failure, connect timeout, or reset.
        let resp = build_error(
            request_id,
            "upstream_unavailable",
            StatusCode::SERVICE_UNAVAILABLE,
        );
        (StatusCode::SERVICE_UNAVAILABLE, None, resp)
    } else if err.is_timeout() {
        // AC-8: read/request timeout exceeded (after connection was established).
        let resp = build_error(request_id, "upstream_timeout", StatusCode::GATEWAY_TIMEOUT);
        (StatusCode::GATEWAY_TIMEOUT, None, resp)
    } else {
        // Other transport error — treat as unavailable.
        let resp = build_error(
            request_id,
            "upstream_unavailable",
            StatusCode::SERVICE_UNAVAILABLE,
        );
        (StatusCode::SERVICE_UNAVAILABLE, None, resp)
    }
}

/// Map a `TokenError` from the ID-token fetch to the AC-6 `reason_code`.
///
/// - `Fetch` (metadata 4xx/5xx or unparseable body) → `"metadata_token_failed"`
/// - `Timeout` → `"timeout"`
/// - `Network` (DNS/TCP/TLS) → `"network"`
#[must_use]
pub fn classify_id_token_error(err: &TokenError) -> &'static str {
    match err {
        TokenError::Fetch => "metadata_token_failed",
        TokenError::Timeout => "timeout",
        TokenError::Network => "network",
    }
}

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

/// Convert an `Instant` elapsed duration to milliseconds as `i64`.
///
/// Latency fits `i64` unless elapsed > 292M years; `unwrap_or` prevents panic
/// on overflow.
#[must_use]
pub fn elapsed_ms(start: std::time::Instant) -> i64 {
    i64::try_from(start.elapsed().as_millis()).unwrap_or(i64::MAX)
}
