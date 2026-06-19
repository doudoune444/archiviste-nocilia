//! Handler for `POST /v1/chat/stream` — forwards to workers `/v1/generate/stream` (CHAT-001).
//!
//! # Security
//! - Request body cap: 1 MiB enforced by `RequestBodyLimitLayer` in `lib.rs`.
//! - Query cap: 4 096 bytes UTF-8 enforced by `parse_and_validate` (reused from chat.rs).
//! - Identity forwarded via `X-User-Tier` / `X-User-Id` headers (SEC-001 AC-14).
//! - SSE response relayed verbatim — no buffering, no re-serialisation.
//! - Error envelope never leaks stack traces, file paths, or upstream bodies (A05).
//! - Log line never contains the raw `query` field (AC-13 / A09).

use axum::{
    extract::{Extension, State},
    http::StatusCode,
    response::Response,
};
use serde_json::json;
use std::sync::Arc;
use std::time::Instant;

use crate::handlers::workers_proxy::{
    build_error, classify_id_token_error, elapsed_ms, map_reqwest_error,
};
use crate::{auth::extractor::AnonIdentity, state::AppState, RequestId};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Maximum inbound query length in UTF-8 bytes (matches chat.rs).
const MAX_QUERY_BYTES: usize = 4_096;

// ---------------------------------------------------------------------------
// Public handler
// ---------------------------------------------------------------------------

/// Handler for `POST /v1/chat/stream`.
///
/// Validates the request, resolves caller identity, fetches a Cloud Run ID token,
/// forwards to workers `/v1/generate/stream`, and relays the SSE byte stream
/// verbatim to the caller. Pre-stream errors return a JSON envelope.
///
/// CHAT-001 design decisions:
/// - No response buffering — `axum::body::Body::from_stream(resp.bytes_stream())`.
/// - No `overhead_header` middleware (it would buffer the stream).
/// - Request body cap (1 MiB) still applied by `RequestBodyLimitLayer`.
pub async fn chat_stream(
    State(state): State<Arc<AppState>>,
    Extension(req_id): Extension<RequestId>,
    Extension(identity): Extension<AnonIdentity>,
    body: axum::body::Bytes,
) -> Response {
    let request_id = req_id.0;
    let start = Instant::now();

    let validated = match parse_and_validate(&body) {
        Ok(r) => r,
        Err(code) => {
            let latency_ms = elapsed_ms(start);
            log_stream_request(&request_id, 0, None, 400, latency_ms);
            return build_error(&request_id, code, StatusCode::BAD_REQUEST);
        }
    };

    let query_len = validated.query.len();
    let url = format!("{}/v1/generate/stream", state.config.workers_url);

    // SEC-006 AC-6: fetch Google-signed ID token; failure → 503 pre-stream JSON.
    let id_token_start = Instant::now();
    let id_token = match state.workers_id_token_provider.fetch_id_token().await {
        Ok(t) => t,
        Err(e) => {
            let latency_ms = elapsed_ms(id_token_start);
            let reason_code = classify_id_token_error(&e);
            tracing::warn!(
                event = "chat_stream.id_token_failed",
                request_id,
                latency_ms,
                reason_code,
            );
            let resp = build_error(
                &request_id,
                "upstream_unavailable",
                StatusCode::SERVICE_UNAVAILABLE,
            );
            log_stream_request(&request_id, query_len, None, 503, elapsed_ms(start));
            return resp;
        }
    };

    let workers_body = json!({
        "query": validated.query,
        "conversation_id": validated.conversation_id,
        "request_id": request_id,
    });

    let result = state
        .http
        .post(&url)
        .header("content-type", "application/json")
        .header("x-request-id", &request_id)
        // SEC-001 AC-14: identity propagated via headers (not JSON body).
        .header("x-user-tier", identity.tier.as_str())
        .header("x-user-id", identity.user_id.to_string())
        // SEC-006 AC-6: ID token for Cloud Run IAM.
        .bearer_auth(secrecy::ExposeSecret::expose_secret(&id_token))
        .json(&workers_body)
        .send()
        .await;

    let upstream = match result {
        Err(ref e) => {
            let (status, _, resp) = map_reqwest_error(e, &request_id);
            log_stream_request(
                &request_id,
                query_len,
                None,
                status.as_u16(),
                elapsed_ms(start),
            );
            return resp;
        }
        Ok(r) => r,
    };

    let upstream_status = upstream.status().as_u16();
    if !upstream.status().is_success() {
        // Pre-stream non-2xx from workers → JSON 502 envelope (not SSE).
        let resp = build_error(&request_id, "upstream_error", StatusCode::BAD_GATEWAY);
        log_stream_request(
            &request_id,
            query_len,
            Some(upstream_status),
            502,
            elapsed_ms(start),
        );
        return resp;
    }

    // Relay the SSE stream verbatim — no buffering.
    log_stream_request(
        &request_id,
        query_len,
        Some(upstream_status),
        200,
        elapsed_ms(start),
    );

    let stream = upstream.bytes_stream();
    axum::response::Response::builder()
        .status(StatusCode::OK)
        .header("content-type", "text/event-stream")
        .header("cache-control", "no-cache")
        .header("x-request-id", &request_id)
        .header("x-content-type-options", "nosniff")
        .body(axum::body::Body::from_stream(stream))
        .unwrap_or_else(|_| build_error(&request_id, "internal", StatusCode::INTERNAL_SERVER_ERROR))
}

// ---------------------------------------------------------------------------
// Validation (reuses same rules as chat.rs)
// ---------------------------------------------------------------------------

struct ValidatedRequest {
    query: String,
    conversation_id: Option<String>,
}

fn parse_and_validate(body: &[u8]) -> Result<ValidatedRequest, &'static str> {
    let value: serde_json::Value = serde_json::from_slice(body).map_err(|_| "invalid_request")?;

    let query = match value.get("query") {
        Some(serde_json::Value::String(s)) => s.clone(),
        _ => return Err("invalid_request"),
    };
    if query.is_empty() || query.len() > MAX_QUERY_BYTES {
        return Err("invalid_request");
    }

    let conversation_id = match value.get("conversation_id") {
        None | Some(serde_json::Value::Null) => None,
        Some(serde_json::Value::String(s)) => uuid::Uuid::parse_str(s)
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
// Logging (A09)
// ---------------------------------------------------------------------------

/// Emit one structured log per request; never logs the raw query (A09).
fn log_stream_request(
    request_id: &str,
    query_len: usize,
    upstream_status: Option<u16>,
    status: u16,
    latency_ms: i64,
) {
    tracing::info!(
        event = "chat_stream",
        request_id,
        query_len,
        upstream_status,
        status,
        latency_ms,
    );
}
