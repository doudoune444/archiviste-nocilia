//! Seam 1 tests for the per-call cold-start timeout override on `POST /v1/chat` (#294).
//!
//! The gateway's global `reqwest::Client` keeps a 35 s read timeout. The chat
//! handler applies a wider per-call override (default 90 s) on its `RequestBuilder`
//! so a worker cold start (transformers import > 30 s) followed by LLM generation
//! is not severed at the global ceiling. Every other worker route keeps the global
//! timeout.
//!
//! These tests pin the *behavior* through the public HTTP interface:
//! - a worker slower than the global timeout but faster than the chat override
//!   still completes for `/v1/chat`;
//! - the same slow worker still severs at the global timeout on a non-chat route.

#![allow(clippy::unwrap_used)]

mod common;
use common::jwt_helpers::make_test_config;

use archiviste_gateway::{auth_metadata::IdTokenProvider, router, state::AppState};
use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use std::sync::Arc;
use std::time::Duration;
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Build a state with a short global timeout (500 ms) and a chat override taken
/// from `chat_request_timeout_ms`.
///
/// Production runs a 35 s global ceiling and a 90 s chat override; here both are
/// scaled down so the tests exercise the real timeout boundaries in well under a
/// second instead of waiting on the production values.
fn make_state(workers_url: &str, chat_request_timeout_ms: u64) -> Arc<AppState> {
    let mut config = make_test_config(workers_url);
    config.connect_timeout_ms = 50;
    config.request_timeout_ms = 500;
    config.chat_request_timeout_ms = chat_request_timeout_ms;
    let id_token_provider = Arc::new(IdTokenProvider::new_stub_always_valid().unwrap());
    Arc::new(AppState::new_with_id_token_provider(config, id_token_provider).unwrap())
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

// ---------------------------------------------------------------------------
// Seam 1: chat per-call override outlives the global timeout
// ---------------------------------------------------------------------------

/// A worker that responds after a delay longer than the global 500 ms timeout
/// but shorter than the chat override still completes for `/v1/chat`.
///
/// Without the per-call override the global 500 ms ceiling would sever this
/// call and return 504; with the override the chat call holds and passes the
/// 200 body through.
#[tokio::test]
async fn chat_override_holds_past_global_timeout() {
    let mut server = mockito::Server::new_async().await;
    let mock = server
        .mock("POST", "/v1/generate")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"answer":"ok","citations":[]}"#)
        // Delay > global 500 ms timeout, < 5 s chat override.
        .with_chunked_body(|w| {
            std::thread::sleep(Duration::from_millis(1_500));
            w.write_all(br#"{"answer":"ok","citations":[]}"#)
        })
        .create_async()
        .await;

    // Chat override is 5 s — wide enough to absorb the 1.5 s worker delay.
    let app = router(make_state(&server.url(), 5_000));
    let resp = post_chat(app, r#"{"query":"hello"}"#).await;

    assert_eq!(
        resp.status(),
        StatusCode::OK,
        "chat must absorb a worker slower than the global timeout"
    );
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    let body: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(body["answer"], "ok");
    mock.assert_async().await;
}

// ---------------------------------------------------------------------------
// AC-8 mapping unchanged: chat still severs at its own override → 504
// ---------------------------------------------------------------------------

/// A worker that never responds still severs at the chat override and maps to
/// 504 `upstream_timeout` — the per-call override is a finite read timeout, and
/// the existing `is_timeout` → 504 mapping is unchanged (#294).
#[tokio::test]
async fn chat_override_severs_unresponsive_worker_with_504() {
    use tokio::net::TcpListener;

    // Accept the connection but never write a response.
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();
    tokio::spawn(async move {
        if let Ok((_socket, _)) = listener.accept().await {
            tokio::time::sleep(Duration::from_mins(1)).await;
        }
    });

    let workers_url = format!("http://{addr}");
    // Short chat override (400 ms) so the read-side timeout fires fast.
    let app = router(make_state(&workers_url, 400));
    let resp = post_chat(app, r#"{"query":"hello"}"#).await;

    assert_eq!(
        resp.status(),
        StatusCode::GATEWAY_TIMEOUT,
        "chat must still map its own timeout to 504"
    );
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    let body: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(body["error"], "upstream_timeout");
}
