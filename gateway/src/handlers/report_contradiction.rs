//! Handler for `POST /v1/report-contradiction` — forwards to workers
//! `/v1/verify-contradiction` (CTR-002).
//!
//! # Security
//! - Body cap: 1 MiB enforced by `RequestBodyLimitLayer` in `lib.rs`.
//! - Claim cap: 4 096 bytes UTF-8 enforced in `validate_request`.
//! - `user_id` and `user_tier` are resolved from `AnonIdentity` extension (SEC-001 AC-14).
//! - Error envelope never leaks stack traces, file paths, or upstream bodies.
//! - Log line never contains the raw `claim` field (A09): only `claim_len`.
//! - `request_id` is always gateway-generated — never from the client body.

use axum::{
    extract::{Extension, State},
    http::StatusCode,
    response::Response,
};
use serde_json::{json, Value};
use std::sync::Arc;
use std::time::Instant;

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

/// Maximum claim length in bytes.
const MAX_CLAIM_BYTES: usize = 4_096;

/// Maximum number of citations allowed.
const MAX_CITATIONS: usize = 50;

/// Maximum number of `chunk_ords` per citation.
const MAX_CHUNK_ORDS: usize = 200;

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/// Validated incoming request after parsing.
struct ValidatedRequest {
    claim: String,
    conversation_id: String,
    /// None when the client omitted citations (no-citation retrieval path in workers).
    citations: Option<Value>,
}

// ---------------------------------------------------------------------------
// Public handler
// ---------------------------------------------------------------------------

/// Handler for `POST /v1/report-contradiction`.
///
/// Validates the request, reads `request_id` from middleware (AC-16 discipline:
/// gateway always generates it — never trusts a client-supplied value), reads
/// the caller identity from `AnonIdentity`, then forwards to workers.
pub async fn report_contradiction(
    State(state): State<Arc<AppState>>,
    Extension(req_id): Extension<RequestId>,
    Extension(identity): Extension<AnonIdentity>,
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

    let claim_len = validated.claim.len();
    let (gateway_status, upstream_status, response) =
        forward_to_workers(&state, &request_id, validated, &identity, workers_cell).await;

    let latency_ms = elapsed_ms(start);
    log_request(
        &request_id,
        claim_len,
        upstream_status,
        gateway_status.as_u16(),
        latency_ms,
    );

    response
}

// ---------------------------------------------------------------------------
// Validation
// ---------------------------------------------------------------------------

/// Parse raw bytes as JSON and validate the contradiction-report request fields.
///
/// Returns `Err(&'static str)` with the error code on validation failure.
fn parse_and_validate(body: &[u8]) -> Result<ValidatedRequest, &'static str> {
    let value: Value = serde_json::from_slice(body).map_err(|_| "invalid_request")?;

    let claim = match value.get("claim") {
        Some(Value::String(s)) => s.clone(),
        _ => return Err("invalid_request"),
    };
    if claim.is_empty() || claim.len() > MAX_CLAIM_BYTES {
        return Err("invalid_request");
    }

    let conversation_id = match value.get("conversation_id") {
        Some(Value::String(s)) => uuid::Uuid::parse_str(s)
            .map(|u| u.to_string())
            .map_err(|_| "invalid_request")?,
        _ => return Err("invalid_request"),
    };

    // Citations are optional (no-citation retrieval path, design decision #5).
    // When present they must be a valid array of ≤50 items; absent/null → None.
    let citations = match value.get("citations") {
        None | Some(Value::Null) => None,
        Some(Value::Array(arr)) => {
            if arr.len() > MAX_CITATIONS {
                return Err("invalid_request");
            }
            validate_citations(arr)?;
            Some(Value::Array(arr.clone()))
        }
        _ => return Err("invalid_request"),
    };

    Ok(ValidatedRequest {
        claim,
        conversation_id,
        citations,
    })
}

/// Validate each citation entry in the array.
fn validate_citations(arr: &[Value]) -> Result<(), &'static str> {
    for citation in arr {
        let Some(Value::String(source_path)) = citation.get("source_path") else {
            return Err("invalid_request");
        };
        if source_path.is_empty() {
            return Err("invalid_request");
        }

        match citation.get("chunk_ords") {
            Some(Value::Array(ords)) => {
                if ords.len() > MAX_CHUNK_ORDS {
                    return Err("invalid_request");
                }
                for ord in ords {
                    match ord {
                        Value::Number(n) => {
                            let v = n.as_i64().ok_or("invalid_request")?;
                            if v < 0 {
                                return Err("invalid_request");
                            }
                        }
                        _ => return Err("invalid_request"),
                    }
                }
            }
            _ => return Err("invalid_request"),
        }
    }
    Ok(())
}

/// Build the JSON body forwarded to workers `/v1/verify-contradiction`.
///
/// When `citations` is `None` (no-citation retrieval path) the field is omitted
/// so workers falls through to its embed-then-retrieve path.
fn build_workers_body(
    claim: &str,
    conversation_id: &str,
    citations: Option<&Value>,
    request_id: &str,
) -> Value {
    let mut body = json!({
        "claim": claim,
        "conversation_id": conversation_id,
        "request_id": request_id,
    });
    if let Some(cits) = citations {
        body["citations"] = cits.clone();
    }
    body
}

// ---------------------------------------------------------------------------
// Forwarding
// ---------------------------------------------------------------------------

/// Forward the validated request to workers `/v1/verify-contradiction`.
async fn forward_to_workers(
    state: &AppState,
    request_id: &str,
    req: ValidatedRequest,
    identity: &AnonIdentity,
    workers_cell: Option<Extension<WorkersCallDuration>>,
) -> (StatusCode, Option<u16>, Response) {
    let url = format!("{}/v1/verify-contradiction", state.config.workers_url);

    // SEC-006: fetch a Google-signed ID-token before the workers call.
    let id_token_start = Instant::now();
    let id_token = match state.workers_id_token_provider.fetch_id_token().await {
        Ok(t) => t,
        Err(e) => {
            let latency_ms = elapsed_ms(id_token_start);
            let reason_code = classify_id_token_error(&e);
            tracing::warn!(
                event = "report_contradiction.id_token_failed",
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

    let workers_body = build_workers_body(
        &req.claim,
        &req.conversation_id,
        req.citations.as_ref(),
        request_id,
    );

    let workers_start = Instant::now();
    let result = state
        .http
        .post(&url)
        .header("content-type", "application/json")
        .header("x-request-id", request_id)
        .header("x-user-tier", identity.tier.as_str())
        .header("x-user-id", identity.user_id.to_string())
        .bearer_auth(secrecy::ExposeSecret::expose_secret(&id_token))
        .json(&workers_body)
        .send()
        .await;

    let outcome = match result {
        Err(ref e) => map_reqwest_error(e, request_id),
        Ok(upstream) => handle_workers_response(upstream, request_id).await,
    };

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
        let gateway_resp = build_error(request_id, "upstream_error", StatusCode::BAD_GATEWAY);
        return (StatusCode::BAD_GATEWAY, Some(upstream_status), gateway_resp);
    }

    match read_capped_body(resp).await {
        Err(()) => {
            tracing::warn!(event = "upstream_body_too_large", request_id);
            let gateway_resp = build_error(request_id, "upstream_error", StatusCode::BAD_GATEWAY);
            (StatusCode::BAD_GATEWAY, Some(upstream_status), gateway_resp)
        }
        Ok(bytes) => {
            let gateway_resp = build_passthrough(request_id, bytes);
            (StatusCode::OK, Some(upstream_status), gateway_resp)
        }
    }
}

// ---------------------------------------------------------------------------
// Logging
// ---------------------------------------------------------------------------

/// Emit exactly one structured log per request (A09).
///
/// The raw `claim` is never logged — only `claim_len`.
fn log_request(
    request_id: &str,
    claim_len: usize,
    upstream_status: Option<u16>,
    status: u16,
    latency_ms: i64,
) {
    tracing::info!(
        event = "report_contradiction",
        request_id,
        claim_len,
        upstream_status,
        status,
        latency_ms,
    );
}
