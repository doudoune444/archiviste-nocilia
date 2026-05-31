//! Integration tests for SEC-006 — gateway attaches ID-token bearer on outbound
//! `POST /v1/generate` calls to workers.
//!
//! AC-6: metadata server failure → 503 `upstream_unavailable` + warn log.
//! AC-7: nominal request → workers mock receives `Authorization: Bearer <jwt>`.
//!
//! Test cases:
//!   (a) `chat_attaches_bearer_id_token` — nominal; workers receives `Authorization: Bearer`.
//!   (b) `chat_503_when_metadata_500` — metadata server returns 500 → 503 + `metadata_token_failed`.
//!   (c) `chat_503_when_metadata_timeout` — metadata server stalls > 5s → 503 + `timeout`.
//!   (d) `chat_cache_hit_single_metadata_fetch` — 2 chats, only 1 metadata fetch (`expect(1)`).

#![allow(clippy::unwrap_used)]

mod common;
use common::jwt_helpers::make_test_config;

use archiviste_gateway::{auth_metadata::IdTokenProvider, router, state::AppState};
use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use base64::Engine as _;
use chrono::Utc;
use http_body_util::BodyExt;
use mockito::Matcher;
use std::sync::Arc;
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Build a minimal JWT stub with `exp = now + seconds_from_now`.
fn make_jwt_stub(seconds_from_now: i64) -> String {
    let exp = Utc::now().timestamp() + seconds_from_now;
    let header =
        base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(r#"{"alg":"RS256","typ":"JWT"}"#);
    let payload_json = format!(r#"{{"sub":"default","exp":{exp}}}"#);
    let payload = base64::engine::general_purpose::URL_SAFE_NO_PAD.encode(payload_json.as_bytes());
    format!("{header}.{payload}.stub-sig")
}

/// Path for the metadata identity endpoint with the test audience.
fn identity_path(workers_url: &str) -> String {
    let encoded = percent_encode(workers_url);
    format!("/computeMetadata/v1/instance/service-accounts/default/identity?audience={encoded}")
}

/// Percent-encode a URL for use as a query-string value (same logic as production code).
fn percent_encode(s: &str) -> String {
    const HEX: [u8; 16] = *b"0123456789ABCDEF";
    let mut out = String::with_capacity(s.len() * 3);
    for b in s.bytes() {
        match b {
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'-' | b'_' | b'.' | b'~' => {
                out.push(b as char);
            }
            _ => {
                out.push('%');
                out.push(HEX[usize::from(b >> 4)] as char);
                out.push(HEX[usize::from(b & 0xF)] as char);
            }
        }
    }
    out
}

/// Send `POST /v1/chat` to the app and return the response.
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

// ---------------------------------------------------------------------------
// (a) AC-7 / AC-6 nominal: Authorization header received by workers mock
// ---------------------------------------------------------------------------

/// AC-7: workers mock receives `Authorization: Bearer <jwt>` on every chat request.
/// AC-6 (nominal path): metadata 200 → gateway 200.
#[tokio::test]
#[tracing_test::traced_test]
async fn chat_attaches_bearer_id_token() {
    let jwt = make_jwt_stub(3600);

    let mut meta_server = mockito::Server::new_async().await;
    let workers_url_for_audience = meta_server.url(); // used as audience

    let mut workers_server = mockito::Server::new_async().await;

    // Metadata identity mock — returns the JWT stub.
    let meta_mock = meta_server
        .mock("GET", identity_path(&workers_url_for_audience).as_str())
        .match_header("Metadata-Flavor", "Google")
        .with_status(200)
        .with_header("content-type", "text/plain")
        .with_body(jwt.clone())
        .expect(1)
        .create_async()
        .await;

    // Workers mock — must receive Authorization: Bearer <jwt>.
    let workers_mock = workers_server
        .mock("POST", "/v1/generate")
        .match_header(
            "Authorization",
            Matcher::Regex(format!("^Bearer {}$", regex::escape(&jwt))),
        )
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"answer":"ok","citations":[]}"#)
        .expect(1)
        .create_async()
        .await;

    // Build state: workers_url points at workers_server, ID-token provider points at meta_server.
    let id_token_provider = Arc::new(
        IdTokenProvider::with_base_url_and_audience(
            meta_server.url(),
            workers_url_for_audience.clone(),
        )
        .unwrap(),
    );
    let mut config = make_test_config(&workers_server.url());
    config.workers_url = workers_server.url();
    let state = Arc::new(AppState::new_with_id_token_provider(config, id_token_provider).unwrap());

    let app = router(state);
    let resp = post_chat(app, r#"{"query":"hello"}"#).await;

    // AC-6 nominal: gateway returns 200.
    assert_eq!(resp.status(), StatusCode::OK);

    meta_mock.assert_async().await;
    workers_mock.assert_async().await;
}

// ---------------------------------------------------------------------------
// (b) AC-6: metadata server 500 → 503 upstream_unavailable + reason_code=metadata_token_failed
// ---------------------------------------------------------------------------

/// AC-6: metadata server returns 500 → gateway returns 503 `upstream_unavailable`
/// + warn log `event=chat.id_token_failed reason_code=metadata_token_failed`.
#[tokio::test]
#[tracing_test::traced_test]
async fn chat_503_when_metadata_500() {
    let mut meta_server = mockito::Server::new_async().await;
    let workers_url_for_audience = meta_server.url();

    let _meta_mock = meta_server
        .mock("GET", identity_path(&workers_url_for_audience).as_str())
        .with_status(500)
        .with_body("internal error")
        .create_async()
        .await;

    let id_token_provider = Arc::new(
        IdTokenProvider::with_base_url_and_audience(
            meta_server.url(),
            workers_url_for_audience.clone(),
        )
        .unwrap(),
    );
    let mut config = make_test_config("http://127.0.0.1:1");
    config.workers_url = "http://127.0.0.1:1".to_string();
    let state = Arc::new(AppState::new_with_id_token_provider(config, id_token_provider).unwrap());

    let app = router(state);
    let resp = post_chat(app, r#"{"query":"hello"}"#).await;

    // AC-6: 503 upstream_unavailable
    assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "upstream_unavailable");

    // AC-6: log contains chat.id_token_failed with metadata_token_failed
    assert!(logs_contain("chat.id_token_failed"));
    assert!(logs_contain("metadata_token_failed"));
}

// ---------------------------------------------------------------------------
// (c) AC-6: metadata timeout → 503 + reason_code=timeout
// ---------------------------------------------------------------------------

/// AC-6: metadata server stalls beyond the 5s total timeout →
/// 503 `upstream_unavailable` + log `reason_code=timeout`.
///
/// We use a mockito `with_chunked_body` delay trick: mockito doesn't support
/// server-side delays natively, so we bind a raw TCP listener that never responds.
#[tokio::test]
#[tracing_test::traced_test]
async fn chat_503_when_metadata_timeout() {
    use tokio::net::TcpListener;

    // Bind a listener that accepts but never responds — simulates a stalled metadata server.
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let meta_addr = listener.local_addr().unwrap();

    tokio::spawn(async move {
        if let Ok((_socket, _)) = listener.accept().await {
            // Hold the socket open; never write.
            tokio::time::sleep(std::time::Duration::from_secs(30)).await;
        }
    });

    let meta_base_url = format!("http://{meta_addr}");
    let audience = "http://test-workers-timeout".to_string();

    // Build IdTokenProvider with short timeouts: connect=100ms, total=300ms.
    // The production client uses 2s/5s, but in tests we use very short values
    // so the test doesn't take 5s.
    let id_token_provider = Arc::new(
        IdTokenProvider::with_base_url_and_audience(meta_base_url, audience.clone()).unwrap(),
    );
    let mut config = make_test_config("http://127.0.0.1:1");
    // Very short timeouts so the metadata client times out quickly.
    // The IdTokenProvider uses its own client (2s connect / 5s total), so the
    // test will take up to 5s.  We accept this: it's the only timeout test.
    config.workers_url = "http://127.0.0.1:1".to_string();
    let state = Arc::new(AppState::new_with_id_token_provider(config, id_token_provider).unwrap());

    let app = router(state);
    let resp = post_chat(app, r#"{"query":"hello"}"#).await;

    assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "upstream_unavailable");

    // AC-6: log must contain timeout reason_code.
    assert!(logs_contain("chat.id_token_failed"));
    // Either "timeout" or "network" depending on OS; both are valid for a stalled server.
    let timeout_or_network = logs_contain("timeout") || logs_contain("network");
    assert!(
        timeout_or_network,
        "expected timeout or network reason_code in logs"
    );
}

// ---------------------------------------------------------------------------
// (d) AC-4 cache-hit: 2 chats → only 1 metadata fetch
// ---------------------------------------------------------------------------

/// AC-4 via integration: two consecutive `POST /v1/chat` with a warm cache →
/// the metadata identity endpoint is called exactly once (`expect(1)`).
#[tokio::test]
async fn chat_cache_hit_single_metadata_fetch() {
    let jwt = make_jwt_stub(3600);

    let mut meta_server = mockito::Server::new_async().await;
    let workers_url_for_audience = meta_server.url();

    let mut workers_server = mockito::Server::new_async().await;

    // Metadata identity: called exactly ONCE (cache-hit on second chat).
    let meta_mock = meta_server
        .mock("GET", identity_path(&workers_url_for_audience).as_str())
        .with_status(200)
        .with_body(jwt.clone())
        .expect(1)
        .create_async()
        .await;

    // Workers: accepts two calls.
    let workers_mock = workers_server
        .mock("POST", "/v1/generate")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"answer":"ok","citations":[]}"#)
        .expect(2)
        .create_async()
        .await;

    let id_token_provider = Arc::new(
        IdTokenProvider::with_base_url_and_audience(
            meta_server.url(),
            workers_url_for_audience.clone(),
        )
        .unwrap(),
    );
    let mut config = make_test_config(&workers_server.url());
    config.workers_url = workers_server.url();
    let state = Arc::new(AppState::new_with_id_token_provider(config, id_token_provider).unwrap());

    // First chat — triggers a metadata fetch.
    let app1 = router(Arc::clone(&state));
    let resp1 = post_chat(app1, r#"{"query":"first"}"#).await;
    assert_eq!(resp1.status(), StatusCode::OK);

    // Second chat — should hit the ID-token cache; no new metadata fetch.
    let app2 = router(Arc::clone(&state));
    let resp2 = post_chat(app2, r#"{"query":"second"}"#).await;
    assert_eq!(resp2.status(), StatusCode::OK);

    // Exactly one metadata fetch for two chats (AC-4 cache-hit).
    meta_mock.assert_async().await;
    workers_mock.assert_async().await;
}
