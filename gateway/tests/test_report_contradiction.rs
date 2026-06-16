//! Integration tests for `POST /v1/report-contradiction` (CTR-002).
//!
//! AC references:
//! - AC-1: valid report → gateway forwards to workers `/v1/verify-contradiction`
//!   with `x-user-id` + `x-user-tier` headers propagated; `request_id` NOT taken from client.
//! - AC-2: passthrough of the 200 verdict body.
//! - AC-3: invalid body (empty claim / bad uuid / empty citations / too many) → 400 `invalid_request`.
//! - AC-4: upstream 4xx/5xx → 502 `upstream_error`; connect failure → 503.

#![allow(clippy::unwrap_used)]

mod common;
use common::jwt_helpers::{make_app_state, make_test_config};

use archiviste_gateway::{auth_metadata::IdTokenProvider, router};
use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use std::sync::Arc;
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn make_state(workers_url: &str) -> Arc<archiviste_gateway::state::AppState> {
    make_app_state(workers_url)
}

/// Build a state with a tight `request_timeout_ms` for timeout tests.
///
/// `connect_timeout_ms` is short (50 ms) so that a loopback-connect never
/// races with the read-side timeout. `request_timeout_ms` (500 ms) is the
/// timeout the test actually exercises.
fn make_state_with_short_timeout(workers_url: &str) -> Arc<archiviste_gateway::state::AppState> {
    let mut config = make_test_config(workers_url);
    config.connect_timeout_ms = 50;
    config.request_timeout_ms = 500;
    let id_token_provider = Arc::new(IdTokenProvider::new_stub_always_valid().unwrap());
    Arc::new(
        archiviste_gateway::state::AppState::new_with_id_token_provider(config, id_token_provider)
            .unwrap(),
    )
}

async fn post_report(app: axum::Router, body: &str) -> axum::response::Response {
    app.oneshot(
        Request::builder()
            .method("POST")
            .uri("/v1/report-contradiction")
            .header("content-type", "application/json")
            .body(Body::from(body.to_string()))
            .unwrap(),
    )
    .await
    .unwrap()
}

async fn body_json(resp: axum::response::Response) -> serde_json::Value {
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap()
}

fn assert_error_envelope(body: &serde_json::Value, expected_code: &str) {
    assert_eq!(body["error"], expected_code);
    let rid = body["request_id"].as_str().unwrap_or("");
    assert_eq!(rid.len(), 36, "request_id must be 36 chars");
    let parts: Vec<&str> = rid.split('-').collect();
    assert_eq!(parts.len(), 5);
}

const VALID_UUID: &str = "550e8400-e29b-41d4-a716-446655440000";

fn valid_payload() -> String {
    format!(
        r#"{{
            "claim": "Nocilia was founded in year 0.",
            "conversation_id": "{VALID_UUID}",
            "citations": [{{"source_path": "lore/chapter1.md", "chunk_ords": [0, 1]}}]
        }}"#
    )
}

// ---------------------------------------------------------------------------
// AC-1 / AC-2: happy path — forwards to workers, passthrough body, headers propagated
// ---------------------------------------------------------------------------

/// AC-1: valid report → gateway forwards to `/v1/verify-contradiction` with
/// `x-user-id` and `x-user-tier` headers, and the gateway-generated `request_id`
/// (never the client-supplied one).
/// AC-2: workers 200 → passthrough verdict body.
#[tokio::test]
async fn ac1_ac2_valid_report_forwarded_to_workers() {
    use std::sync::{Arc as StdArc, Mutex};

    let captured_tier: StdArc<Mutex<Option<String>>> = StdArc::new(Mutex::new(None));
    let captured_uid: StdArc<Mutex<Option<String>>> = StdArc::new(Mutex::new(None));
    let captured_req_id: StdArc<Mutex<Option<String>>> = StdArc::new(Mutex::new(None));

    let mut server = mockito::Server::new_async().await;
    let tier_cap = StdArc::clone(&captured_tier);
    let uid_cap = StdArc::clone(&captured_uid);
    let rid_cap = StdArc::clone(&captured_req_id);

    let verdict_body = r#"{"contradiction_confirmed":true,"confirmations":2,"ticket_action":"created","ticket_id":"550e8400-e29b-41d4-a716-446655440001"}"#;

    let _mock = server
        .mock("POST", "/v1/verify-contradiction")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body_from_request(move |req| {
            if let Some(t) = req.header("x-user-tier").first() {
                *tier_cap.lock().unwrap() = Some(t.to_str().unwrap_or("").to_string());
            }
            if let Some(u) = req.header("x-user-id").first() {
                *uid_cap.lock().unwrap() = Some(u.to_str().unwrap_or("").to_string());
            }
            if let Some(r) = req.header("x-request-id").first() {
                *rid_cap.lock().unwrap() = Some(r.to_str().unwrap_or("").to_string());
            }
            verdict_body.into()
        })
        .create_async()
        .await;

    let app = router(make_state(&server.url()));
    let resp = post_report(app, &valid_payload()).await;

    // AC-2: passthrough 200 body
    assert_eq!(resp.status(), StatusCode::OK);
    let body = body_json(resp).await;
    assert_eq!(body["contradiction_confirmed"], true);
    assert_eq!(body["ticket_action"], "created");

    // AC-1: identity headers forwarded
    let tier = captured_tier.lock().unwrap().clone().unwrap_or_default();
    assert_eq!(tier, "anonymous", "x-user-tier must be anonymous");
    let uid = captured_uid.lock().unwrap().clone().unwrap_or_default();
    assert_eq!(uid.len(), 36, "x-user-id must be a UUID");

    // AC-1: gateway-generated request_id (not from client)
    let req_id_sent = captured_req_id.lock().unwrap().clone().unwrap_or_default();
    assert_eq!(
        req_id_sent.len(),
        36,
        "x-request-id forwarded to workers must be a UUID"
    );
}

// ---------------------------------------------------------------------------
// AC-3: validation failures → 400 invalid_request, workers NOT called
// ---------------------------------------------------------------------------

/// AC-3: empty claim → 400 `invalid_request`.
#[tokio::test]
async fn ac3_empty_claim_returns_400() {
    // Port 1 = loopback refuse — workers must NOT be called.
    let app = router(make_state("http://127.0.0.1:1"));
    let payload = format!(
        r#"{{"claim":"","conversation_id":"{VALID_UUID}","citations":[{{"source_path":"f.md","chunk_ords":[0]}}]}}"#
    );
    let resp = post_report(app, &payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

/// AC-3: claim > 4096 bytes → 400.
#[tokio::test]
async fn ac3_claim_too_long_returns_400() {
    let long_claim = "a".repeat(4097);
    let payload = format!(
        r#"{{"claim":"{long_claim}","conversation_id":"{VALID_UUID}","citations":[{{"source_path":"f.md","chunk_ords":[0]}}]}}"#
    );
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_report(app, &payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

/// AC-3: bad `conversation_id` (not a UUID) → 400.
#[tokio::test]
async fn ac3_bad_conversation_id_returns_400() {
    let payload = r#"{"claim":"x","conversation_id":"not-a-uuid","citations":[{"source_path":"f.md","chunk_ords":[0]}]}"#;
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_report(app, payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

/// AC-3: empty citations array → 400.
#[tokio::test]
async fn ac3_empty_citations_returns_400() {
    let payload = format!(r#"{{"claim":"x","conversation_id":"{VALID_UUID}","citations":[]}}"#);
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_report(app, &payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

/// AC-3: more than 50 citations → 400.
#[tokio::test]
async fn ac3_too_many_citations_returns_400() {
    let citations: String = (0..51)
        .map(|i| format!(r#"{{"source_path":"file{i}.md","chunk_ords":[0]}}"#))
        .collect::<Vec<_>>()
        .join(",");
    let payload =
        format!(r#"{{"claim":"x","conversation_id":"{VALID_UUID}","citations":[{citations}]}}"#);
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_report(app, &payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

/// AC-3: citation with empty `source_path` → 400.
#[tokio::test]
async fn ac3_citation_empty_source_path_returns_400() {
    let payload = format!(
        r#"{{"claim":"x","conversation_id":"{VALID_UUID}","citations":[{{"source_path":"","chunk_ords":[0]}}]}}"#
    );
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_report(app, &payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

/// AC-3: negative `chunk_ord` → 400.
#[tokio::test]
async fn ac3_negative_chunk_ord_returns_400() {
    let payload = format!(
        r#"{{"claim":"x","conversation_id":"{VALID_UUID}","citations":[{{"source_path":"f.md","chunk_ords":[-1]}}]}}"#
    );
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_report(app, &payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

/// AC-3: missing required field (no claim) → 400.
#[tokio::test]
async fn ac3_missing_claim_returns_400() {
    let payload = format!(
        r#"{{"conversation_id":"{VALID_UUID}","citations":[{{"source_path":"f.md","chunk_ords":[0]}}]}}"#
    );
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_report(app, &payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

// ---------------------------------------------------------------------------
// AC-4: upstream errors
// ---------------------------------------------------------------------------

/// AC-4: workers connection refused → 503 `upstream_unavailable`.
#[tokio::test]
async fn ac4_connect_failure_returns_503() {
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_report(app, &valid_payload()).await;
    assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "upstream_unavailable");
}

/// AC-4: workers 500 → 502 `upstream_error`.
#[tokio::test]
async fn ac4_workers_500_returns_502() {
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/verify-contradiction")
        .with_status(500)
        .with_body(r#"{"error":"internal"}"#)
        .create_async()
        .await;

    let app = router(make_state(&server.url()));
    let resp = post_report(app, &valid_payload()).await;
    assert_eq!(resp.status(), StatusCode::BAD_GATEWAY);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "upstream_error");
}

/// AC-4: workers 400 → 502 (not passthrough).
#[tokio::test]
async fn ac4_workers_400_returns_502() {
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/verify-contradiction")
        .with_status(400)
        .with_body(r#"{"error":"invalid_claim"}"#)
        .create_async()
        .await;

    let app = router(make_state(&server.url()));
    let resp = post_report(app, &valid_payload()).await;
    assert_eq!(resp.status(), StatusCode::BAD_GATEWAY);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "upstream_error");
}

// ---------------------------------------------------------------------------
// MED-2: fan-out cap — 201 chunk_ords → 400, workers NOT called
// ---------------------------------------------------------------------------

/// AC-3 (fan-out cap): a citation with 201 `chunk_ords` exceeds `MAX_CHUNK_ORDS`=200
/// and must return 400 `invalid_request` without calling workers.
/// Port 1 = loopback refuse — verifies workers is not called.
#[tokio::test]
async fn ac3_too_many_chunk_ords_returns_400() {
    // 201 chunk_ords in one citation — exceeds the MAX_CHUNK_ORDS=200 A04/DoS guard.
    let ords: String = (0..201_u32)
        .map(|i| i.to_string())
        .collect::<Vec<_>>()
        .join(",");
    let payload = format!(
        r#"{{"claim":"x","conversation_id":"{VALID_UUID}","citations":[{{"source_path":"f.md","chunk_ords":[{ords}]}}]}}"#
    );
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_report(app, &payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

// ---------------------------------------------------------------------------
// LOW: route body-limit → uniform 400 envelope (not raw 413)
// ---------------------------------------------------------------------------

/// A04: body > 1 MiB → `RequestBodyLimitLayer` fires; `handle_body_limit_error`
/// rewrites 413 → 400 `invalid_request` with uniform error envelope.
/// Workers must NOT be called (port 1 = loopback refuse).
#[tokio::test]
async fn low_body_too_large_returns_400_envelope() {
    let app = router(make_state("http://127.0.0.1:1"));
    let big_body = vec![b'x'; 1_048_577]; // 1 MiB + 1
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/report-contradiction")
                .header("content-type", "application/json")
                .body(Body::from(big_body))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    // handle_body_limit_error rewrites 413 → 400 with `invalid_request` code.
    assert_eq!(body["error"], "invalid_request");
}

// ---------------------------------------------------------------------------
// LOW: upstream timeout → 504 upstream_timeout
// ---------------------------------------------------------------------------

/// A04: workers never responds within the configured timeout → 504 `upstream_timeout`.
/// Uses a TCP listener that accepts but never sends, with a 500ms request timeout.
#[tokio::test]
async fn low_upstream_timeout_returns_504() {
    use tokio::net::TcpListener;

    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();

    // Accept in background — never respond, hold the socket open.
    tokio::spawn(async move {
        if let Ok((_socket, _)) = listener.accept().await {
            tokio::time::sleep(std::time::Duration::from_mins(1)).await;
        }
    });

    let workers_url = format!("http://{addr}");
    let app = router(make_state_with_short_timeout(&workers_url));
    let resp = post_report(app, &valid_payload()).await;

    assert_eq!(resp.status(), StatusCode::GATEWAY_TIMEOUT);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "upstream_timeout");
}

// ---------------------------------------------------------------------------
// LOW: overhead header present (OPS-001a parity with chat_router)
// ---------------------------------------------------------------------------

/// OPS-001 AC-4: `X-Gateway-Overhead-Ms` is present on 200 responses from
/// `POST /v1/report-contradiction` (`overhead_header` middleware parity with chat).
#[tokio::test]
async fn low_overhead_header_present_on_200() {
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/verify-contradiction")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(
            r#"{"contradiction_confirmed":false,"confirmations":0,"ticket_action":"none","ticket_id":null}"#,
        )
        .create_async()
        .await;

    let app = router(make_state(&server.url()));
    let resp = post_report(app, &valid_payload()).await;

    assert_eq!(resp.status(), StatusCode::OK);
    assert!(
        resp.headers().contains_key("x-gateway-overhead-ms"),
        "X-Gateway-Overhead-Ms must be present on report-contradiction 200"
    );
}

/// OPS-001 AC-4: `X-Gateway-Overhead-Ms` is present on 400 (validation failure,
/// workers not called) — mirrors the chat suite ac4e test.
#[tokio::test]
async fn low_overhead_header_present_on_400() {
    let app = router(make_state("http://127.0.0.1:1"));
    let payload = format!(
        r#"{{"claim":"","conversation_id":"{VALID_UUID}","citations":[{{"source_path":"f.md","chunk_ords":[0]}}]}}"#
    );
    let resp = post_report(app, &payload).await;

    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    assert!(
        resp.headers().contains_key("x-gateway-overhead-ms"),
        "X-Gateway-Overhead-Ms must be present even on 400 (no workers call)"
    );
}
