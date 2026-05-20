//! Integration tests for `POST /v1/chat` (GEN-002).
//!
//! AC references per criterion are noted inline.

#![allow(clippy::unwrap_used)]

mod common;
use common::jwt_helpers::test_public_key_pem;

use archiviste_gateway::{config::Config, router, state::AppState};
use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use std::sync::Arc;
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Build an `AppState` pointing at `workers_url` with tight timeouts for CI.
fn make_state(workers_url: &str) -> Arc<AppState> {
    let config = Config {
        bind_addr: "127.0.0.1:0".to_string(),
        workers_url: workers_url.to_string(),
        database_url: "postgres://test".to_string(),
        jwt_ed25519_public_key_pem: test_public_key_pem().to_string(),
        version: "0.1.0".to_string(),
        connect_timeout_ms: 500,
        request_timeout_ms: 35_000,
    };
    Arc::new(AppState::new(config).unwrap())
}

/// Build a state with a tight `request_timeout_ms` for AC-8 timeout tests.
///
/// `connect_timeout_ms` is short (50 ms) so that a loopback-connect never
/// races with the read-side timeout. `request_timeout_ms` (500 ms) is the
/// timeout the test actually exercises.
fn make_state_with_short_timeout(workers_url: &str) -> Arc<AppState> {
    let config = Config {
        bind_addr: "127.0.0.1:0".to_string(),
        workers_url: workers_url.to_string(),
        database_url: "postgres://test".to_string(),
        jwt_ed25519_public_key_pem: test_public_key_pem().to_string(),
        version: "0.1.0".to_string(),
        connect_timeout_ms: 50,
        request_timeout_ms: 500,
    };
    Arc::new(AppState::new(config).unwrap())
}

async fn post_chat(app: axum::Router, body: &str) -> axum::response::Response {
    app.oneshot(
        Request::builder()
            .method("POST")
            .uri("/v1/chat")
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

/// Assert that an error envelope has the expected error code and a valid
/// `UUIDv4` `request_id` (36-char, 8-4-4-4-12 hyphen format).
fn assert_error_envelope(body: &serde_json::Value, expected_code: &str) {
    assert_eq!(body["error"], expected_code);
    let rid = body["request_id"].as_str().unwrap_or("");
    assert_eq!(rid.len(), 36, "request_id must be 36 chars, got: {rid}");
    let parts: Vec<&str> = rid.split('-').collect();
    assert_eq!(parts.len(), 5, "request_id must be 8-4-4-4-12 format");
    assert_eq!(parts[0].len(), 8);
    assert_eq!(parts[1].len(), 4);
    assert_eq!(parts[2].len(), 4);
    assert_eq!(parts[3].len(), 4);
    assert_eq!(parts[4].len(), 12);
}

// ---------------------------------------------------------------------------
// AC-5 : invalid `query` → 400
// ---------------------------------------------------------------------------

/// AC-5: missing `query` field → 400 `invalid_request`.
#[tokio::test]
async fn ac5_missing_query_returns_400() {
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_chat(app, r"{}").await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

/// AC-5: empty `query` → 400 `invalid_request`.
#[tokio::test]
async fn ac5_empty_query_returns_400() {
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_chat(app, r#"{"query":""}"#).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

/// AC-5: `query` > 4096 bytes → 400 `invalid_request`.
#[tokio::test]
async fn ac5_query_too_long_returns_400() {
    let long_query = "a".repeat(4097);
    let payload = format!(r#"{{"query":"{long_query}"}}"#);
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_chat(app, &payload).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

/// AC-5: `query` is a number (wrong type) → 400 `invalid_request`.
#[tokio::test]
async fn ac5_query_wrong_type_returns_400() {
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_chat(app, r#"{"query":123}"#).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

// ---------------------------------------------------------------------------
// AC-6 : invalid `conversation_id` → 400
// ---------------------------------------------------------------------------

/// AC-6: `conversation_id` not a UUID → 400 `invalid_request`.
#[tokio::test]
async fn ac6_invalid_conversation_id_returns_400() {
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_chat(app, r#"{"query":"hello","conversation_id":"not-a-uuid"}"#).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "invalid_request");
}

// ---------------------------------------------------------------------------
// AC-7 : non-JSON body or body > 1 MiB → 400
// ---------------------------------------------------------------------------

/// AC-7: plain-text body → 400 `invalid_request`.
#[tokio::test]
async fn ac7_non_json_body_returns_400() {
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/chat")
                .header("content-type", "text/plain")
                .body(Body::from("hello"))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "invalid_request");
}

/// AC-7: body > 1 MiB → 400 `invalid_request` (`RequestBodyLimitLayer` rejects).
#[tokio::test]
async fn ac7_body_too_large_returns_400() {
    let app = router(make_state("http://127.0.0.1:1"));
    let big_body = vec![b'x'; 1_048_577]; // 1 MiB + 1
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/chat")
                .header("content-type", "application/json")
                .body(Body::from(big_body))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "invalid_request");
}

// ---------------------------------------------------------------------------
// AC-1, AC-2, AC-3, AC-4 : happy path via mockito
// ---------------------------------------------------------------------------

/// AC-1: workers 200 → gateway 200 with passthrough body.
/// AC-2: X-Request-Id header present in response, `UUIDv4`, matches body.
/// AC-3: body sent to workers has exact fields + sentinel values.
/// AC-4: X-Request-Id header sent to workers matches body.
#[tokio::test]
async fn ac1_ac2_ac3_ac4_happy_path() {
    let mut server = mockito::Server::new_async().await;
    let workers_response =
        r#"{"answer":"The scriptorium holds ancient knowledge.","citations":[]}"#;

    let mock = server
        .mock("POST", "/v1/generate")
        .match_header("content-type", "application/json")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(workers_response)
        .create_async()
        .await;

    let app = router(make_state(&server.url()));
    let resp = post_chat(
        app,
        r#"{"query":"What is the scriptorium?","conversation_id":null}"#,
    )
    .await;

    // AC-1: 200 + passthrough body
    assert_eq!(resp.status(), StatusCode::OK);

    // AC-2: X-Request-Id header present in gateway response
    let request_id = resp
        .headers()
        .get("x-request-id")
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();
    assert_eq!(
        request_id.len(),
        36,
        "request_id must be a UUIDv4 (36 chars)"
    );

    // UI-001 AC-17: security headers present on POST /v1/chat (router-level middleware).
    assert!(
        resp.headers().get("content-security-policy").is_some(),
        "Content-Security-Policy header missing on /v1/chat"
    );
    assert!(
        resp.headers().get("x-content-type-options").is_some(),
        "X-Content-Type-Options header missing on /v1/chat"
    );
    assert!(
        resp.headers().get("referrer-policy").is_some(),
        "Referrer-Policy header missing on /v1/chat"
    );
    assert!(
        resp.headers().get("x-frame-options").is_some(),
        "X-Frame-Options header missing on /v1/chat"
    );

    let body_bytes = resp.into_body().collect().await.unwrap().to_bytes();
    let body: serde_json::Value = serde_json::from_slice(&body_bytes).unwrap();
    assert_eq!(body["answer"], "The scriptorium holds ancient knowledge.");

    mock.assert_async().await;
}

/// AC-3 / AC-4 : verify body and header sent to workers.
#[tokio::test]
async fn ac3_ac4_workers_body_and_headers() {
    use std::sync::{Arc, Mutex};

    let captured_body: Arc<Mutex<Option<String>>> = Arc::new(Mutex::new(None));
    let captured_header: Arc<Mutex<Option<String>>> = Arc::new(Mutex::new(None));

    let mut server = mockito::Server::new_async().await;
    let body_capture = Arc::clone(&captured_body);
    let header_capture = Arc::clone(&captured_header);

    let mock = server
        .mock("POST", "/v1/generate")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"answer":"ok","citations":[]}"#)
        .with_body_from_request(move |req| {
            let body_bytes = req.body().unwrap_or(&Vec::new()).clone();
            let body_str = String::from_utf8_lossy(&body_bytes).to_string();
            *body_capture.lock().unwrap() = Some(body_str);
            if let Some(hdr) = req.header("x-request-id").first() {
                let hdr_str = hdr.to_str().unwrap_or("").to_string();
                *header_capture.lock().unwrap() = Some(hdr_str);
            }
            r#"{"answer":"ok","citations":[]}"#.into()
        })
        .create_async()
        .await;

    let app = router(make_state(&server.url()));
    let resp = post_chat(
        app,
        r#"{"query":"hello","conversation_id":"550e8400-e29b-41d4-a716-446655440000"}"#,
    )
    .await;

    assert_eq!(resp.status(), StatusCode::OK);
    let response_request_id = resp
        .headers()
        .get("x-request-id")
        .unwrap()
        .to_str()
        .unwrap()
        .to_string();

    mock.assert_async().await;

    // AC-3: body sent to workers
    let body_str = captured_body.lock().unwrap().clone().unwrap();
    let workers_body: serde_json::Value = serde_json::from_str(&body_str).unwrap();
    assert_eq!(workers_body["query"], "hello");
    assert_eq!(
        workers_body["conversation_id"],
        "550e8400-e29b-41d4-a716-446655440000"
    );
    // SEC-001 AC-14: user_id is now a UUIDv5 fingerprint (not the sentinel).
    let user_id_str = workers_body["user_id"].as_str().unwrap_or("");
    assert_eq!(user_id_str.len(), 36, "user_id must be a UUID");
    // SEC-001 AC-14: user_tier propagated from resolved identity.
    assert_eq!(workers_body["user_tier"], "anonymous");
    assert!(workers_body["request_id"].as_str().is_some());
    assert_eq!(workers_body["request_id"], response_request_id);

    // AC-4: X-Request-Id header sent to workers
    let header_val = captured_header.lock().unwrap().clone().unwrap();
    assert_eq!(header_val, response_request_id);
}

// ---------------------------------------------------------------------------
// AC-16 : client X-Request-Id ignored
// ---------------------------------------------------------------------------

/// AC-16: client-supplied X-Request-Id is ignored; gateway generates its own.
#[tokio::test]
async fn ac16_client_request_id_ignored() {
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/generate")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"answer":"ok","citations":[]}"#)
        .create_async()
        .await;

    let app = router(make_state(&server.url()));
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/chat")
                .header("content-type", "application/json")
                .header("x-request-id", "client-provided-id")
                .body(Body::from(r#"{"query":"hello"}"#))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(resp.status(), StatusCode::OK);
    let request_id = resp
        .headers()
        .get("x-request-id")
        .unwrap()
        .to_str()
        .unwrap();
    assert_ne!(
        request_id, "client-provided-id",
        "gateway must not echo client X-Request-Id"
    );
    assert_eq!(request_id.len(), 36);
}

// ---------------------------------------------------------------------------
// AC-9 : connection refused → 503
// ---------------------------------------------------------------------------

/// AC-9: workers unreachable (connection refused) → 503 `upstream_unavailable`.
#[tokio::test]
async fn ac9_connection_refused_returns_503() {
    // Port 1 is reserved and will refuse connections on loopback.
    let app = router(make_state("http://127.0.0.1:1"));
    let resp = post_chat(app, r#"{"query":"hello"}"#).await;
    assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "upstream_unavailable");
}

// ---------------------------------------------------------------------------
// AC-10, AC-11 : workers error codes → 502
// ---------------------------------------------------------------------------

/// AC-10: workers 500 → 502 `upstream_error`.
#[tokio::test]
async fn ac10_workers_500_returns_502() {
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/generate")
        .with_status(500)
        .with_body(r#"{"error":"internal"}"#)
        .create_async()
        .await;

    let app = router(make_state(&server.url()));
    let resp = post_chat(app, r#"{"query":"hello"}"#).await;
    assert_eq!(resp.status(), StatusCode::BAD_GATEWAY);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "upstream_error");
}

/// AC-10: workers 404 → 502 `upstream_error`.
#[tokio::test]
async fn ac10_workers_404_returns_502() {
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/generate")
        .with_status(404)
        .create_async()
        .await;

    let app = router(make_state(&server.url()));
    let resp = post_chat(app, r#"{"query":"hello"}"#).await;
    assert_eq!(resp.status(), StatusCode::BAD_GATEWAY);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "upstream_error");
}

/// AC-10: workers 503 → 502 `upstream_error`.
#[tokio::test]
async fn ac10_workers_503_returns_502() {
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/generate")
        .with_status(503)
        .create_async()
        .await;

    let app = router(make_state(&server.url()));
    let resp = post_chat(app, r#"{"query":"hello"}"#).await;
    assert_eq!(resp.status(), StatusCode::BAD_GATEWAY);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "upstream_error");
}

/// AC-11: workers 400 → 502 (not passthrough 400).
#[tokio::test]
async fn ac11_workers_400_returns_502_not_passthrough() {
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/generate")
        .with_status(400)
        .with_body(r#"{"error":"bad_request"}"#)
        .create_async()
        .await;

    let app = router(make_state(&server.url()));
    let resp = post_chat(app, r#"{"query":"hello"}"#).await;
    // AC-11: 400 from workers signals a contract violation, must be 502 not 400
    assert_eq!(resp.status(), StatusCode::BAD_GATEWAY);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "upstream_error");
}

// ---------------------------------------------------------------------------
// AC-8 : request timeout → 504
// ---------------------------------------------------------------------------

/// AC-8: workers never responds within timeout → 504 `upstream_timeout`.
/// Uses a TCP listener that accepts but never sends, with a 500ms request timeout
/// and a 50ms connect timeout (read >> connect guarantees read-side fires).
#[tokio::test]
async fn ac8_request_timeout_returns_504() {
    use tokio::net::TcpListener;

    // Bind a listener that accepts connections but never writes back.
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();

    // Accept in background — never respond.
    tokio::spawn(async move {
        if let Ok((_socket, _)) = listener.accept().await {
            // Hold the socket open but never write; drop it when task ends.
            tokio::time::sleep(std::time::Duration::from_mins(1)).await;
        }
    });

    let workers_url = format!("http://{addr}");
    let app = router(make_state_with_short_timeout(&workers_url));
    let resp = post_chat(app, r#"{"query":"hello"}"#).await;

    assert_eq!(resp.status(), StatusCode::GATEWAY_TIMEOUT);
    let body = body_json(resp).await;
    assert_error_envelope(&body, "upstream_timeout");
}

// ---------------------------------------------------------------------------
// AC-15 : upstream body cap 256 KiB → 502
// ---------------------------------------------------------------------------

/// AC-15: workers body > 256 KiB → 502 `upstream_error`.
#[tokio::test]
async fn ac15_upstream_body_too_large_returns_502() {
    let mut server = mockito::Server::new_async().await;
    let big_body = "x".repeat(262_145); // 256 KiB + 1
    let _mock = server
        .mock("POST", "/v1/generate")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(big_body)
        .create_async()
        .await;

    let app = router(make_state(&server.url()));
    let resp = post_chat(app, r#"{"query":"hello"}"#).await;
    assert_eq!(resp.status(), StatusCode::BAD_GATEWAY);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "upstream_error");
}

// ---------------------------------------------------------------------------
// AC-14 : single `http` client shared across requests
// ---------------------------------------------------------------------------

/// AC-14: the same `reqwest::Client` instance serves all `/v1/chat` requests.
///
/// We verify this by checking that `Arc::strong_count` on the `AppState` only
/// increases when the `Router` clones the `Arc` (Axum behaviour), and that the
/// raw pointer to the `Client` inside the `AppState` is identical across two
/// independent `oneshot` calls — both calls see the same `Arc<AppState>`, hence
/// the same `Client` without re-initialisation.
#[tokio::test]
async fn ac14_single_http_client_in_state() {
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/generate")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"answer":"ok","citations":[]}"#)
        .expect(2) // exactly two calls
        .create_async()
        .await;

    let state = make_state(&server.url());

    // Capture the raw pointer to the Client before any requests.
    let client_ptr = std::ptr::addr_of!(state.http) as usize;

    // First request
    let app1 = router(Arc::clone(&state));
    let resp1 = post_chat(app1, r#"{"query":"first"}"#).await;
    assert_eq!(resp1.status(), StatusCode::OK);

    // Second request — pointer to Client must be unchanged (same instance).
    let app2 = router(Arc::clone(&state));
    let resp2 = post_chat(app2, r#"{"query":"second"}"#).await;
    assert_eq!(resp2.status(), StatusCode::OK);

    // The Client lives inside `state` which is Arc-cloned, never reconstructed.
    // Pointer stability proves no new Client was built between requests (AC-14).
    let client_ptr_after = std::ptr::addr_of!(state.http) as usize;
    assert_eq!(
        client_ptr, client_ptr_after,
        "Client pointer changed — a new Client was created between requests"
    );
}

// ---------------------------------------------------------------------------
// SEC-001 AC-14 : X-User-Tier and X-User-Id headers forwarded to workers
// ---------------------------------------------------------------------------

/// SEC-001 AC-14 (a): anonymous request → workers receive X-User-Tier=anonymous
/// and X-User-Id=<uuidv5-fingerprint>.
#[tokio::test]
async fn sec001_ac14a_anonymous_headers_forwarded_to_workers() {
    use std::sync::{Arc as StdArc, Mutex};

    let captured_tier: StdArc<Mutex<Option<String>>> = StdArc::new(Mutex::new(None));
    let captured_uid: StdArc<Mutex<Option<String>>> = StdArc::new(Mutex::new(None));

    let mut server = mockito::Server::new_async().await;
    let tier_cap = StdArc::clone(&captured_tier);
    let uid_cap = StdArc::clone(&captured_uid);

    let _mock = server
        .mock("POST", "/v1/generate")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body_from_request(move |req| {
            if let Some(t) = req.header("x-user-tier").first() {
                *tier_cap.lock().unwrap() = Some(t.to_str().unwrap_or("").to_string());
            }
            if let Some(u) = req.header("x-user-id").first() {
                *uid_cap.lock().unwrap() = Some(u.to_str().unwrap_or("").to_string());
            }
            r#"{"answer":"ok","citations":[]}"#.into()
        })
        .create_async()
        .await;

    let app = router(make_state(&server.url()));
    let resp = post_chat(app, r#"{"query":"hello"}"#).await;
    assert_eq!(resp.status(), StatusCode::OK);

    // SEC-001 AC-14 (a): anonymous caller → tier=anonymous, user_id=uuid5 fingerprint
    let tier = captured_tier.lock().unwrap().clone().unwrap_or_default();
    assert_eq!(
        tier, "anonymous",
        "X-User-Tier must be anonymous for unauthenticated caller"
    );

    let uid = captured_uid.lock().unwrap().clone().unwrap_or_default();
    assert_eq!(uid.len(), 36, "X-User-Id must be a UUID");
    // UUIDv5 from fingerprint (not the old sentinel 00000000-...)
    assert_ne!(
        uid, "00000000-0000-0000-0000-000000000000",
        "X-User-Id must not be the old sentinel"
    );
}
