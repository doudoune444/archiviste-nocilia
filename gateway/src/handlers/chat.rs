//! Handler for `POST /v1/chat` — forwards to workers `/v1/generate` (GEN-002).
//!
//! # Security
//! - Body cap: 1 MiB enforced by `RequestBodyLimitLayer` in `lib.rs`.
//! - Query cap: 4 096 bytes UTF-8 enforced in `validate_request`.
//! - `user_id` and `user_tier` are resolved from `AnonIdentity` extension (SEC-001 AC-14).
//! - Error envelope never leaks stack traces, file paths, or upstream bodies.
//! - Log line never contains the raw `query` field (AC-13 / A09).

use axum::{
    extract::{Extension, State},
    http::StatusCode,
    response::Response,
};
use serde_json::{json, Value};
use std::sync::Arc;
use std::time::{Duration, Instant};

use uuid::Uuid;

use crate::handlers::workers_proxy::{
    build_error, build_passthrough, classify_id_token_error, elapsed_ms, map_reqwest_error,
    read_capped_body,
};
use crate::{
    auth::extractor::AnonIdentity, middleware::WorkersCallDuration, state::AppState, RequestId,
};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Maximum query length in bytes (AC-5).
const MAX_QUERY_BYTES: usize = 4_096;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/// Validated incoming request fields after parsing.
struct ValidatedRequest {
    query: String,
    conversation_id: Option<String>,
}

// ---------------------------------------------------------------------------
// Public handler
// ---------------------------------------------------------------------------

/// Handler for `POST /v1/chat`.
///
/// Validates the request, reads the `request_id` from middleware extension (R2),
/// reads the caller identity from the `AnonIdentity` extension (SEC-001 AC-14),
/// then forwards to workers. Returns passthrough body on success or a uniform
/// error envelope on failure.
///
/// AC-16: `X-Request-Id` from the client is never forwarded — `attach_request_id`
/// middleware generates the id upstream and it is read here via `Extension<RequestId>`.
pub async fn chat(
    State(state): State<Arc<AppState>>,
    Extension(req_id): Extension<RequestId>,
    Extension(identity): Extension<AnonIdentity>,
    // OPS-001a: optional cell inserted by `overhead_header` middleware.
    // Absent when the middleware is not applied (e.g. non-chat routes in tests).
    workers_cell: Option<Extension<WorkersCallDuration>>,
    body: axum::body::Bytes,
) -> Response {
    let request_id = req_id.0;
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
        forward_to_workers(&state, &request_id, validated, &identity, workers_cell).await;

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
/// Writes the workers call duration into `workers_cell` (if present) so that
/// `overhead_header` middleware can subtract it from total elapsed time.
async fn forward_to_workers(
    state: &AppState,
    request_id: &str,
    req: ValidatedRequest,
    identity: &AnonIdentity,
    workers_cell: Option<Extension<WorkersCallDuration>>,
) -> (StatusCode, Option<u16>, Response) {
    let url = format!("{}/v1/generate", state.config.workers_url);

    // SEC-006 AC-6: fetch a Google-signed ID-token before the workers call.
    // On any failure, return 503 immediately — no fallback, no retry (spec non-goal).
    let id_token_start = Instant::now();
    let id_token = match state.workers_id_token_provider.fetch_id_token().await {
        Ok(t) => t,
        Err(e) => {
            let latency_ms = elapsed_ms(id_token_start);
            let reason_code = classify_id_token_error(&e);
            tracing::warn!(
                event = "chat.id_token_failed",
                request_id,
                latency_ms,
                reason_code,
            );
            let resp = build_error(
                request_id,
                "upstream_unavailable",
                StatusCode::SERVICE_UNAVAILABLE,
            );
            return (StatusCode::SERVICE_UNAVAILABLE, None, resp);
        }
    };

    // SEC-001 AC-14: propagate resolved identity to workers via outbound headers.
    // Plan line 24: "propager X-User-Tier + X-User-Id au lieu de user_id/user_tier dans le JSON body".
    // Identity is NOT duplicated in the JSON body — headers are the canonical transport.
    let workers_body = json!({
        "query": req.query,
        "conversation_id": req.conversation_id,
        "request_id": request_id,
    });

    // OPS-001a: measure the workers call duration for overhead attribution.
    // The timer covers the full interaction: request send + response headers + body read.
    let workers_start = Instant::now();
    let result = state
        .http
        .post(&url)
        // #294: per-call timeout override on the chat path only. The global client
        // keeps its 35 s read ceiling; here we widen to `chat_request_timeout_ms`
        // (default 90 s) because a worker cold start imports transformers (> 30 s at
        // boot) before generating, and severing at 35 s turns a cold start into a
        // spurious 504. This exceeds the 30 s external-call cap in `security.md` by
        // design — the gap is intentional and flagged in the PR, not a silent
        // workaround. Every other worker route keeps the global ceiling.
        .timeout(Duration::from_millis(state.config.chat_request_timeout_ms))
        .header("content-type", "application/json")
        .header("x-request-id", request_id) // AC-4: observational header
        // SEC-001 AC-14: identity propagated via headers only (not JSON body).
        .header("x-user-tier", identity.tier.as_str())
        .header("x-user-id", identity.user_id.to_string())
        // SEC-006 AC-6: attach Google-signed ID-token for Cloud Run IAM.
        .bearer_auth(secrecy::ExposeSecret::expose_secret(&id_token))
        .json(&workers_body)
        .send()
        .await;

    let outcome = match result {
        Err(ref e) => map_reqwest_error(e, request_id),
        Ok(upstream) => handle_workers_response(upstream, request_id).await,
    };

    // Write the full workers duration (including body read) to the shared slot.
    let workers_duration = workers_start.elapsed();
    if let Some(Extension(cell)) = workers_cell {
        cell.set(workers_duration);
    }

    outcome
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
