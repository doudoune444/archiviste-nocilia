//! Handler for `POST /v1/chat` — forwards to workers `/v1/generate` (GEN-002).
//!
//! # Security
//! - Body cap: 1 MiB enforced by `RequestBodyLimitLayer` in `lib.rs`.
//! - Query cap: 4 096 bytes UTF-8 enforced in `validate_request`.
//! - `user_id` and `user_tier` are hardcoded sentinels (phase 1, vision §73).
//! - Error envelope never leaks stack traces, file paths, or upstream bodies.
//! - Log line never contains the raw `query` field (AC-13 / A09).

use axum::{
    extract::State,
    http::{HeaderMap, HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use serde::Serialize;
use serde_json::{json, Value};
use std::sync::Arc;
use std::time::Instant;
use uuid::Uuid;

use crate::state::AppState;

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Maximum query length in bytes (AC-5).
const MAX_QUERY_BYTES: usize = 4_096;

/// Maximum upstream response body size in bytes (AC-15).
const MAX_UPSTREAM_BODY: usize = 262_144; // 256 KiB

/// Sentinel `user_id` for phase-1 anonymous requests (AC-3).
const USER_ID_SENTINEL: &str = "00000000-0000-0000-0000-000000000000";

/// Sentinel `user_tier` for phase-1 anonymous requests (AC-3).
const USER_TIER_ANON: &str = "anonymous";

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/// Validated incoming request fields after parsing.
struct ValidatedRequest {
    query: String,
    conversation_id: Option<String>,
}

/// Uniform error envelope returned on any non-200 response (AC-12).
#[derive(Debug, Serialize)]
pub struct ErrorBody {
    /// Machine-readable error code.
    pub error: &'static str,
    /// Gateway-generated `UUIDv4` request identifier.
    pub request_id: String,
}

// ---------------------------------------------------------------------------
// Public handler
// ---------------------------------------------------------------------------

/// Handler for `POST /v1/chat`.
///
/// Validates the request, generates a `request_id`, then forwards to workers.
/// Returns passthrough body on success or a uniform error envelope on failure.
pub async fn chat(
    State(state): State<Arc<AppState>>,
    _headers: HeaderMap,
    body: axum::body::Bytes,
) -> Response {
    // AC-16: always generate our own request_id; any client X-Request-Id is ignored.
    let request_id = Uuid::new_v4().to_string();
    let start = Instant::now();

    let validated = match parse_and_validate(&body) {
        Ok(r) => r,
        Err(code) => {
            let latency_ms = elapsed_ms(start);
            log_request(&request_id, 0, None, 400, latency_ms);
            return build_error(&request_id, code, StatusCode::BAD_REQUEST);
        }
    };

    let query_len = validated.query.len();
    let (gateway_status, upstream_status, response) =
        forward_to_workers(&state, &request_id, validated).await;

    let latency_ms = elapsed_ms(start);
    log_request(
        &request_id,
        query_len,
        upstream_status,
        gateway_status.as_u16(),
        latency_ms,
    );

    response
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

/// Parse raw bytes as JSON and validate the chat request fields.
///
/// Returns `Err(&'static str)` with the error code on validation failure.
fn parse_and_validate(body: &[u8]) -> Result<ValidatedRequest, &'static str> {
    let value: Value = serde_json::from_slice(body).map_err(|_| "invalid_request")?;

    let query = match value.get("query") {
        Some(Value::String(s)) => s.clone(),
        _ => return Err("invalid_request"),
    };

    if query.is_empty() || query.len() > MAX_QUERY_BYTES {
        return Err("invalid_request");
    }

    let conversation_id = match value.get("conversation_id") {
        None | Some(Value::Null) => None,
        Some(Value::String(s)) => Uuid::parse_str(s)
            .map(|u| Some(u.to_string()))
            .map_err(|_| "invalid_request")?,
        _ => return Err("invalid_request"),
    };

    Ok(ValidatedRequest {
        query,
        conversation_id,
    })
}

// ---------------------------------------------------------------------------
// Forwarding
// ---------------------------------------------------------------------------

/// Forward the validated request to workers.
///
/// Returns `(gateway_status, upstream_status_option, response)`.
async fn forward_to_workers(
    state: &AppState,
    request_id: &str,
    req: ValidatedRequest,
) -> (StatusCode, Option<u16>, Response) {
    let url = format!("{}/v1/generate", state.config.workers_url);

    let workers_body = json!({
        "query": req.query,
        "conversation_id": req.conversation_id,
        "user_id": USER_ID_SENTINEL,
        "user_tier": USER_TIER_ANON,
        "request_id": request_id,
    });

    let result = state
        .http
        .post(&url)
        .header("content-type", "application/json")
        .header("x-request-id", request_id) // AC-4: observational header
        .json(&workers_body)
        .send()
        .await;

    match result {
        Err(ref e) => map_reqwest_error(e, request_id),
        Ok(resp) => handle_workers_response(resp, request_id).await,
    }
}

/// Map a `reqwest::Error` to the appropriate gateway error.
///
/// Priority: connection errors (AC-9) are checked before timeout (AC-8)
/// because a connect-phase timeout sets both `is_connect()` and `is_timeout()`.
fn map_reqwest_error(
    err: &reqwest::Error,
    request_id: &str,
) -> (StatusCode, Option<u16>, Response) {
    if err.is_connect() {
        // AC-9: connection refused, DNS failure, connect timeout, or reset
        let resp = build_error(
            request_id,
            "upstream_unavailable",
            StatusCode::SERVICE_UNAVAILABLE,
        );
        (StatusCode::SERVICE_UNAVAILABLE, None, resp)
    } else if err.is_timeout() {
        // AC-8: read/request timeout exceeded (after connection was established)
        let resp = build_error(request_id, "upstream_timeout", StatusCode::GATEWAY_TIMEOUT);
        (StatusCode::GATEWAY_TIMEOUT, None, resp)
    } else {
        // Other transport error — treat as unavailable
        let resp = build_error(
            request_id,
            "upstream_unavailable",
            StatusCode::SERVICE_UNAVAILABLE,
        );
        (StatusCode::SERVICE_UNAVAILABLE, None, resp)
    }
}

/// Process the workers HTTP response.
async fn handle_workers_response(
    resp: reqwest::Response,
    request_id: &str,
) -> (StatusCode, Option<u16>, Response) {
    let upstream_status = resp.status().as_u16();

    if !resp.status().is_success() {
        // AC-10 / AC-11: any 4xx/5xx from workers → 502 upstream_error
        let gateway_resp = build_error(request_id, "upstream_error", StatusCode::BAD_GATEWAY);
        return (StatusCode::BAD_GATEWAY, Some(upstream_status), gateway_resp);
    }

    match read_capped_body(resp).await {
        Err(()) => {
            // AC-15: body exceeded 256 KiB cap
            tracing::warn!(event = "upstream_body_too_large", request_id);
            let gateway_resp = build_error(request_id, "upstream_error", StatusCode::BAD_GATEWAY);
            (StatusCode::BAD_GATEWAY, Some(upstream_status), gateway_resp)
        }
        Ok(bytes) => {
            // AC-1 / R5: passthrough byte-for-byte, no re-serialisation
            let gateway_resp = build_passthrough(request_id, bytes);
            (StatusCode::OK, Some(upstream_status), gateway_resp)
        }
    }
}

/// Read up to `MAX_UPSTREAM_BODY` bytes.  Returns `Err(())` if cap exceeded.
async fn read_capped_body(resp: reqwest::Response) -> Result<axum::body::Bytes, ()> {
    let bytes = resp.bytes().await.map_err(|_| ())?;
    if bytes.len() > MAX_UPSTREAM_BODY {
        return Err(());
    }
    Ok(bytes)
}

// ---------------------------------------------------------------------------
// Logging (AC-13)
// ---------------------------------------------------------------------------

/// Emit exactly one structured log per request (AC-13 / A09).
///
/// The raw `query` string is never logged — only `query_len`.
fn log_request(
    request_id: &str,
    query_len: usize,
    upstream_status: Option<u16>,
    status: u16,
    latency_ms: i64,
) {
    tracing::info!(
        event = "chat",
        request_id,
        query_len,
        upstream_status,
        status,
        latency_ms,
    );
}

// ---------------------------------------------------------------------------
// Response builders
// ---------------------------------------------------------------------------

/// Build an error response with the uniform envelope (AC-12).
fn build_error(request_id: &str, code: &'static str, status: StatusCode) -> Response {
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
fn build_passthrough(request_id: &str, bytes: axum::body::Bytes) -> Response {
    Response::builder()
        .status(StatusCode::OK)
        .header("content-type", "application/json; charset=utf-8")
        .header("x-request-id", request_id)
        .body(axum::body::Body::from(bytes))
        .unwrap_or_else(|_| build_error(request_id, "internal", StatusCode::INTERNAL_SERVER_ERROR))
}

// ---------------------------------------------------------------------------
// Utility
// ---------------------------------------------------------------------------

fn elapsed_ms(start: Instant) -> i64 {
    i64::try_from(start.elapsed().as_millis()).unwrap_or(i64::MAX)
}
