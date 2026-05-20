//! Property tests for `POST /v1/chat` error envelope invariant (GEN-002).
//!
//! INV-GATEWAY-ERROR-ENVELOPE (AC-12): every error response from the gateway
//! matches the envelope `{"error":"<code>","request_id":"<uuidv4>"}` with no
//! extra fields and no leaked internals.
//!
//! This invariant is local to the gateway crate. It is NOT listed in
//! `specs/properties.md` (humain-only); see plan GEN-002 R4. If the reviewer
//! requests promotion to the central registry, open a dedicated follow-up ticket.

#![allow(clippy::unwrap_used)]

mod common;
use common::jwt_helpers::make_test_config;

use archiviste_gateway::{router, state::AppState};
use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use proptest::prelude::*;
use std::sync::Arc;
use tokio::runtime::Runtime;
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn make_state_for_property(workers_url: &str) -> Arc<AppState> {
    let mut config = make_test_config(workers_url);
    config.connect_timeout_ms = 200;
    // 1000 ms gives headroom on loaded CI runners (MEDIUM finding #7).
    config.request_timeout_ms = 1_000;
    Arc::new(AppState::new(config).unwrap())
}

/// Valid error code values per the spec.
const VALID_ERROR_CODES: &[&str] = &[
    "invalid_request",
    "upstream_timeout",
    "upstream_unavailable",
    "upstream_error",
    "internal",
];

/// Validate that `body_str` is a conforming error envelope: exactly two keys
/// (`error` and `request_id`), `error` in the allowed set, `request_id`
/// is a 36-char `UUIDv4` string. No extra fields (no leak).
///
/// Returns `Ok(())` on success or an `Err(String)` describing the violation.
fn check_error_envelope(body_str: &str) -> Result<(), String> {
    let value: serde_json::Value = serde_json::from_str(body_str)
        .map_err(|_| format!("error body must be valid JSON: {body_str}"))?;
    let Some(obj) = value.as_object() else {
        return Err(format!("error body must be a JSON object: {body_str}"));
    };

    // Exactly 2 fields — no extra keys (no leak of stack, host, query, etc.)
    if obj.len() != 2 {
        return Err(format!(
            "error envelope must have exactly 2 fields, got: {body_str}"
        ));
    }

    // `error` field must be a valid code
    let Some(error_code) = obj["error"].as_str() else {
        return Err(format!("error.error must be a string: {body_str}"));
    };
    if !VALID_ERROR_CODES.contains(&error_code) {
        return Err(format!("unknown error code '{error_code}': {body_str}"));
    }

    // `request_id` must be a 36-char `UUIDv4` string
    let Some(request_id) = obj["request_id"].as_str() else {
        return Err(format!("error.request_id must be a string: {body_str}"));
    };
    if request_id.len() != 36 {
        return Err(format!(
            "request_id must be 36 chars, got {}: {body_str}",
            request_id.len()
        ));
    }
    // Basic UUID format check: 8-4-4-4-12 with hyphens at positions 8,13,18,23
    let parts: Vec<&str> = request_id.split('-').collect();
    if parts.len() != 5
        || parts[0].len() != 8
        || parts[1].len() != 4
        || parts[2].len() != 4
        || parts[3].len() != 4
        || parts[4].len() != 12
    {
        return Err(format!(
            "request_id must be 8-4-4-4-12 UUID format: {body_str}"
        ));
    }
    Ok(())
}

fn is_error_status(status: StatusCode) -> bool {
    matches!(
        status,
        StatusCode::BAD_REQUEST
            | StatusCode::SERVICE_UNAVAILABLE
            | StatusCode::BAD_GATEWAY
            | StatusCode::GATEWAY_TIMEOUT
            | StatusCode::INTERNAL_SERVER_ERROR
    )
}

// ---------------------------------------------------------------------------
// Strategy: invalid client payloads → always 400
// ---------------------------------------------------------------------------

proptest! {
    #![proptest_config(ProptestConfig::with_cases(50))]

    /// INV-GATEWAY-ERROR-ENVELOPE: for any arbitrary byte sequence as body,
    /// the gateway always responds with a conforming error envelope — no raw
    /// body, stack trace, or internal detail leaks through (AC-12).
    ///
    /// Arbitrary bytes cover: empty, valid JSON with wrong types, binary noise,
    /// UTF-8 strings, oversized JSON, and everything in between.
    #[test]
    fn prop_invalid_body_error_envelope(
        raw_bytes in prop::collection::vec(prop::num::u8::ANY, 0..1024),
    ) {
        let rt = Runtime::new().unwrap();
        rt.block_on(async {
            let state = make_state_for_property("http://127.0.0.1:1");
            let app = router(state);

            let resp = app
                .oneshot(
                    Request::builder()
                        .method("POST")
                        .uri("/v1/chat")
                        .header("content-type", "application/json")
                        .body(Body::from(raw_bytes))
                        .unwrap(),
                )
                .await
                .unwrap();

            let status = resp.status();
            prop_assert!(is_error_status(status), "expected error status, got {status}");

            let bytes = resp.into_body().collect().await.unwrap().to_bytes();
            let body_str = String::from_utf8(bytes.to_vec()).unwrap();
            // INV-GATEWAY-ERROR-ENVELOPE
            check_error_envelope(&body_str).map_err(TestCaseError::fail)?;
            Ok(())
        })?;
    }

    /// INV-GATEWAY-ERROR-ENVELOPE: workers upstream errors (4xx/5xx) always
    /// produce a conforming envelope — no upstream body leaks through.
    #[test]
    fn prop_upstream_error_envelope(
        upstream_status in prop::sample::select(vec![400u16, 401, 403, 404, 422, 500, 502, 503]),
    ) {
        let rt = Runtime::new().unwrap();
        rt.block_on(async {
            let mut server = mockito::Server::new_async().await;

            // Upstream returns a body with sensitive internals — must not leak.
            let _mock = server
                .mock("POST", "/v1/generate")
                .with_status(upstream_status as usize)
                .with_body(r#"{"error":"internal","stack":"secret/path/to/file.rs:42"}"#)
                .create_async()
                .await;

            let state = make_state_for_property(&server.url());
            let app = router(state);

            let resp = app
                .oneshot(
                    Request::builder()
                        .method("POST")
                        .uri("/v1/chat")
                        .header("content-type", "application/json")
                        .body(Body::from(r#"{"query":"test"}"#))
                        .unwrap(),
                )
                .await
                .unwrap();

            let status = resp.status();
            prop_assert!(is_error_status(status), "expected error for upstream {upstream_status}");

            let bytes = resp.into_body().collect().await.unwrap().to_bytes();
            let body_str = String::from_utf8(bytes.to_vec()).unwrap();

            // INV-GATEWAY-ERROR-ENVELOPE: no leak, exact envelope
            check_error_envelope(&body_str).map_err(TestCaseError::fail)?;
            Ok(())
        })?;
    }
}
