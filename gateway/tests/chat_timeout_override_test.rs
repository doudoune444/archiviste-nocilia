//! Seam 1 integration tests for the per-call timeout override on `POST /v1/chat` (#294).
//!
//! Behavior under test: the `/v1/chat` worker call overrides the global HTTP client
//! timeout with a larger per-call cap (~90 s) so that a slow worker (cold start +
//! generation) is not cut off where the global timeout would have severed it. Every
//! other worker route keeps the global timeout — verified here via `/v1/chat/stream`.

#![allow(clippy::unwrap_used)]

mod common;
use common::jwt_helpers::make_test_config;

use archiviste_gateway::{auth_metadata::IdTokenProvider, router, state::AppState};
use axum::body::Body;
use axum::http::{Request, StatusCode};
use std::sync::Arc;
use std::time::Duration;
use tokio::io::{AsyncReadExt, AsyncWriteExt};
use tokio::net::TcpListener;
use tower::ServiceExt;

/// Global request timeout used by every worker route in these tests (milliseconds).
///
/// Deliberately short so the contrast route severs quickly in CI. The chat override
/// is far larger, so a worker that responds between this value and the override is
/// cut on other routes but held on `/v1/chat`.
const GLOBAL_TIMEOUT_MS: u64 = 600;

/// Worker response delay (milliseconds): beyond the global cut, well under the override.
const SLOW_WORKER_DELAY_MS: u64 = 1_500;

/// Build state whose global HTTP client timeout is `GLOBAL_TIMEOUT_MS`.
fn make_state_with_global_timeout(workers_url: &str) -> Arc<AppState> {
    let mut config = make_test_config(workers_url);
    config.connect_timeout_ms = 200;
    config.request_timeout_ms = GLOBAL_TIMEOUT_MS;
    let id_token_provider = Arc::new(IdTokenProvider::new_stub_always_valid().unwrap());
    Arc::new(AppState::new_with_id_token_provider(config, id_token_provider).unwrap())
}

/// Spawn a one-shot HTTP server on loopback that accepts a connection, waits
/// `delay`, then writes a fixed 200 JSON response. Returns the bound URL.
///
/// Used instead of `mockito` because mockito 1.x has no response-delay primitive.
async fn spawn_slow_worker(delay: Duration) -> String {
    let listener = TcpListener::bind("127.0.0.1:0").await.unwrap();
    let addr = listener.local_addr().unwrap();

    tokio::spawn(async move {
        if let Ok((mut socket, _)) = listener.accept().await {
            // Drain the inbound request so the client write side completes before
            // we delay (an unread request can surface as a connection reset).
            let mut scratch = [0u8; 4096];
            let _ = socket.read(&mut scratch).await;
            tokio::time::sleep(delay).await;
            let body = r#"{"answer":"slow but alive","citations":[]}"#;
            let response = format!(
                "HTTP/1.1 200 OK\r\ncontent-type: application/json\r\ncontent-length: {}\r\nconnection: close\r\n\r\n{}",
                body.len(),
                body
            );
            let _ = socket.write_all(response.as_bytes()).await;
            let _ = socket.flush().await;
        }
    });

    format!("http://{addr}")
}

async fn post(app: axum::Router, uri: &str, body: &str) -> axum::response::Response {
    app.oneshot(
        Request::builder()
            .method("POST")
            .uri(uri)
            .header("content-type", "application/json")
            .body(Body::from(body.to_string()))
            .unwrap(),
    )
    .await
    .unwrap()
}

/// `/v1/chat` holds past the global timeout because of the per-call override.
///
/// The worker responds after `SLOW_WORKER_DELAY_MS` (> `GLOBAL_TIMEOUT_MS`); without
/// the override this would surface as 504 `upstream_timeout`. With the override it
/// returns 200 with the passthrough body.
#[tokio::test]
async fn chat_holds_past_global_timeout_via_per_call_override() {
    let workers_url = spawn_slow_worker(Duration::from_millis(SLOW_WORKER_DELAY_MS)).await;
    let app = router(make_state_with_global_timeout(&workers_url));

    let resp = post(app, "/v1/chat", r#"{"query":"hello"}"#).await;

    assert_eq!(
        resp.status(),
        StatusCode::OK,
        "chat must survive a slow worker beyond the global timeout via the per-call override"
    );
}

/// Another worker route (`/v1/chat/stream`) still severs at the global timeout.
///
/// Same slow worker, same global timeout, no per-call override → 504 `upstream_timeout`.
#[tokio::test]
async fn other_worker_route_still_severs_at_global_timeout() {
    let workers_url = spawn_slow_worker(Duration::from_millis(SLOW_WORKER_DELAY_MS)).await;
    let app = router(make_state_with_global_timeout(&workers_url));

    let resp = post(app, "/v1/chat/stream", r#"{"query":"hello"}"#).await;

    assert_eq!(
        resp.status(),
        StatusCode::GATEWAY_TIMEOUT,
        "non-chat worker routes must keep the global timeout (no per-call override)"
    );
    let bytes = axum::body::to_bytes(resp.into_body(), usize::MAX)
        .await
        .unwrap();
    let body: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(body["error"], "upstream_timeout");
}
