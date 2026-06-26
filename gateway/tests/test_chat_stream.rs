//! Integration tests for `POST /v1/chat/stream` (CHAT-001).
//!
//! AC references:
//! - CHAT-001 AC-1: SSE event grammar meta -> token* -> done/error relayed verbatim.
//! - CHAT-001 AC-2: pre-stream workers non-2xx → JSON 502 envelope, no SSE.
//! - CHAT-001 AC-3: mid-stream worker error event flows through; stream terminates.
//! - CHAT-001 AC-5: gateway validation failure → JSON 400 (not SSE).

#![allow(clippy::unwrap_used)]

mod common;
use common::jwt_helpers::make_app_state;

use archiviste_gateway::router;
use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

async fn post_chat_stream(app: axum::Router, body: &str) -> axum::response::Response {
    app.oneshot(
        Request::builder()
            .method("POST")
            .uri("/v1/chat/stream")
            .header("content-type", "application/json")
            .body(Body::from(body.to_string()))
            .unwrap(),
    )
    .await
    .unwrap()
}

async fn body_text(resp: axum::response::Response) -> String {
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    String::from_utf8_lossy(&bytes).into_owned()
}

async fn body_json(resp: axum::response::Response) -> serde_json::Value {
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap()
}

// ---------------------------------------------------------------------------
// CHAT-001 AC-5: pre-stream gateway validation failures → 400 JSON (not SSE)
// ---------------------------------------------------------------------------

/// AC-5: missing query → 400 JSON envelope, not SSE.
#[tokio::test]
async fn ac5_missing_query_returns_400_json() {
    let app = router(make_app_state("http://127.0.0.1:1"));
    let resp = post_chat_stream(app, r"{}").await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    // Must be JSON, not text/event-stream.
    let ct = resp
        .headers()
        .get("content-type")
        .unwrap()
        .to_str()
        .unwrap();
    assert!(
        ct.contains("application/json"),
        "expected JSON content-type, got: {ct}"
    );
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    let body: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(body["error"], "invalid_request");
}

/// AC-5: empty query → 400 JSON.
#[tokio::test]
async fn ac5_empty_query_returns_400_json() {
    let app = router(make_app_state("http://127.0.0.1:1"));
    let resp = post_chat_stream(app, r#"{"query":""}"#).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "invalid_request");
}

/// AC-5: query > 4096 bytes → 400 JSON.
#[tokio::test]
async fn ac5_query_too_long_returns_400_json() {
    let long_query = "a".repeat(4097);
    let payload = format!(r#"{{"query":"{long_query}"}}"#);
    let app = router(make_app_state("http://127.0.0.1:1"));
    let resp = post_chat_stream(app, &payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "invalid_request");
}

// ---------------------------------------------------------------------------
// CHAT-001 AC-2: pre-stream workers non-2xx → JSON 502 envelope
// ---------------------------------------------------------------------------

/// AC-2 (pre-stream): workers returns 500 → gateway returns 502 JSON (not SSE).
#[tokio::test]
async fn ac2_workers_500_returns_502_json() {
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/generate/stream")
        .with_status(500)
        .with_body(r#"{"error":"internal"}"#)
        .create_async()
        .await;

    let app = router(make_app_state(&server.url()));
    let resp = post_chat_stream(app, r#"{"query":"hello"}"#).await;
    assert_eq!(resp.status(), StatusCode::BAD_GATEWAY);
    let ct = resp
        .headers()
        .get("content-type")
        .unwrap()
        .to_str()
        .unwrap();
    assert!(
        ct.contains("application/json"),
        "expected JSON not SSE, got: {ct}"
    );
    let body = body_json(resp).await;
    assert_eq!(body["error"], "upstream_error");
}

/// AC-2: workers returns 400 → gateway 502 JSON.
#[tokio::test]
async fn ac2_workers_400_returns_502_json() {
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/generate/stream")
        .with_status(400)
        .with_body(r#"{"error":"invalid_request"}"#)
        .create_async()
        .await;

    let app = router(make_app_state(&server.url()));
    let resp = post_chat_stream(app, r#"{"query":"hello"}"#).await;
    assert_eq!(resp.status(), StatusCode::BAD_GATEWAY);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "upstream_error");
}

// ---------------------------------------------------------------------------
// CHAT-001 AC-1, AC-3: happy path — SSE events relayed verbatim
// ---------------------------------------------------------------------------

/// AC-1: workers emits meta -> token* -> done → gateway relays verbatim, status 200.
/// AC-3: a mid-stream error event flows through and stream terminates.
#[tokio::test]
async fn ac1_sse_stream_relayed_verbatim() {
    // Simulate a workers SSE response: meta, 2 tokens, done.
    let sse_body = concat!(
        "event: meta\ndata: {\"mode\":\"canon\",\"conversation_id\":\"44444444-4444-4444-8444-444444444445\",\"request_id\":\"33333333-3333-4333-8333-333333333334\"}\n\n",
        "event: token\ndata: {\"text\":\"Hello\"}\n\n",
        "event: token\ndata: {\"text\":\" world\"}\n\n",
        "event: done\ndata: {\"citations\":[],\"usage\":{\"prompt_tokens\":10,\"completion_tokens\":5,\"cost_eur\":null},\"retrieve_ms\":12,\"llm_ms\":100}\n\n",
    );

    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/generate/stream")
        .with_status(200)
        .with_header("content-type", "text/event-stream")
        .with_body(sse_body)
        .create_async()
        .await;

    let app = router(make_app_state(&server.url()));
    let resp = post_chat_stream(app, r#"{"query":"hello"}"#).await;

    // AC-1: gateway returns 200 with text/event-stream.
    assert_eq!(resp.status(), StatusCode::OK);
    let ct = resp
        .headers()
        .get("content-type")
        .unwrap()
        .to_str()
        .unwrap();
    assert!(
        ct.contains("text/event-stream"),
        "expected text/event-stream, got: {ct}"
    );

    // Security header.
    assert_eq!(
        resp.headers()
            .get("x-content-type-options")
            .unwrap()
            .to_str()
            .unwrap(),
        "nosniff"
    );

    // Body contains meta and done events verbatim.
    let text = body_text(resp).await;
    assert!(text.contains("event: meta"), "meta event missing");
    assert!(text.contains("event: token"), "token event missing");
    assert!(text.contains("event: done"), "done event missing");
    assert!(text.contains("\"mode\":\"canon\""), "mode field missing");
}

/// #354: a `done` event carrying structured `followups` is relayed verbatim to the front.
#[tokio::test]
async fn done_event_with_followups_relayed_verbatim() {
    let sse_body = concat!(
        "event: meta\ndata: {\"mode\":\"canon\",\"conversation_id\":\"44444444-4444-4444-8444-444444444445\",\"request_id\":\"33333333-3333-4333-8333-333333333334\"}\n\n",
        "event: token\ndata: {\"text\":\"Hello\"}\n\n",
        "event: done\ndata: {\"citations\":[],\"usage\":{\"prompt_tokens\":10,\"completion_tokens\":5,\"cost_eur\":null},\"retrieve_ms\":12,\"llm_ms\":100,\"followups\":[\"Qui a fonde Nocilia ?\",\"Quand l'Archiviste est-il apparu ?\"]}\n\n",
    );

    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/generate/stream")
        .with_status(200)
        .with_header("content-type", "text/event-stream")
        .with_body(sse_body)
        .create_async()
        .await;

    let app = router(make_app_state(&server.url()));
    let resp = post_chat_stream(app, r#"{"query":"hello"}"#).await;

    assert_eq!(resp.status(), StatusCode::OK);
    let text = body_text(resp).await;
    assert!(text.contains("event: done"), "done event missing");
    assert!(text.contains("\"followups\""), "followups field missing");
    assert!(
        text.contains("Quand l'Archiviste est-il apparu ?"),
        "follow-up question missing from relayed done event"
    );
}

/// AC-3: a workers `error` SSE event flows through verbatim; stream terminates.
#[tokio::test]
async fn ac3_worker_error_event_relayed_verbatim() {
    // Workers emits meta then error (e.g. LLM timeout mid-stream).
    let sse_body = concat!(
        "event: meta\ndata: {\"mode\":\"canon\",\"conversation_id\":\"44444444-4444-4444-8444-444444444445\",\"request_id\":\"33333333-3333-4333-8333-333333333334\"}\n\n",
        "event: error\ndata: {\"error\":\"llm_timeout\"}\n\n",
    );

    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/generate/stream")
        .with_status(200)
        .with_header("content-type", "text/event-stream")
        .with_body(sse_body)
        .create_async()
        .await;

    let app = router(make_app_state(&server.url()));
    let resp = post_chat_stream(app, r#"{"query":"hello"}"#).await;

    assert_eq!(resp.status(), StatusCode::OK);
    let text = body_text(resp).await;
    // AC-3: error event flows through; no hang (stream terminates).
    assert!(text.contains("event: error"), "error event missing");
    assert!(text.contains("llm_timeout"), "error code missing");
}

// ---------------------------------------------------------------------------
// Connection-refused → 503 (same as /v1/chat)
// ---------------------------------------------------------------------------

/// Workers unreachable → 503 JSON.
#[tokio::test]
async fn connection_refused_returns_503() {
    let app = router(make_app_state("http://127.0.0.1:1"));
    let resp = post_chat_stream(app, r#"{"query":"hello"}"#).await;
    assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "upstream_unavailable");
}
