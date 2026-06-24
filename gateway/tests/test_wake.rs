//! Integration tests for #293 — `GET /v1/wake` worker pre-warm route.
//!
//! Seam 1: drive `router(make_state(...))` via `oneshot`, worker mocked with `mockito`.
//!
//! Acceptance criteria covered:
//!   - `GET /v1/wake` mounted in the public group, reachable without authentication.
//!   - Handler proxies to `GET {workers_url}/health` carrying the bearer ID-token.
//!   - Warm worker (2xx) → `204` empty body.
//!   - Worker failure → `503` with uniform `{error, request_id}` envelope, no upstream leak.

#![allow(clippy::unwrap_used)]

mod common;
use common::jwt_helpers::make_test_config;

use archiviste_gateway::{auth_metadata::IdTokenProvider, router, state::AppState};
use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use http_body_util::BodyExt;
use mockito::Matcher;
use std::sync::Arc;
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Send `GET /v1/wake` to the app and return the response.
async fn get_wake(app: axum::Router) -> axum::response::Response {
    app.oneshot(
        Request::builder()
            .method("GET")
            .uri("/v1/wake")
            .body(Body::empty())
            .unwrap(),
    )
    .await
    .unwrap()
}

async fn body_json(resp: axum::response::Response) -> serde_json::Value {
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap()
}

/// Build state wiring `workers_url` at `workers_server` and the ID-token provider
/// at a stub that never contacts a metadata server.
fn make_state_with_stub_token(workers_url: &str) -> Arc<AppState> {
    let id_token_provider = Arc::new(IdTokenProvider::new_stub_always_valid().unwrap());
    let mut config = make_test_config(workers_url);
    config.workers_url = workers_url.to_string();
    Arc::new(AppState::new_with_id_token_provider(config, id_token_provider).unwrap())
}

// ---------------------------------------------------------------------------
// Warm worker → 204, proxies to /health with bearer, reachable anonymously
// ---------------------------------------------------------------------------

/// Warm worker responds 2xx on `/health` → gateway returns `204` with no body.
/// The handler must carry `Authorization: Bearer <token>` and reach `/health`.
/// No auth header is sent by the test client → confirms the route is public.
#[tokio::test]
async fn wake_returns_204_and_proxies_to_health_with_bearer() {
    let mut workers_server = mockito::Server::new_async().await;

    let workers_mock = workers_server
        .mock("GET", "/health")
        .match_header("Authorization", Matcher::Regex("^Bearer .+$".to_string()))
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"status":"ok"}"#)
        .expect(1)
        .create_async()
        .await;

    let state = make_state_with_stub_token(&workers_server.url());
    let app = router(state);

    let resp = get_wake(app).await;

    assert_eq!(resp.status(), StatusCode::NO_CONTENT);
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    assert!(bytes.is_empty(), "204 response must have an empty body");

    workers_mock.assert_async().await;
}

// ---------------------------------------------------------------------------
// Worker failure → 503 uniform envelope, no upstream detail leak
// ---------------------------------------------------------------------------

/// Worker `/health` returns 500 → gateway returns `503` with `{error, request_id}`
/// and never echoes the upstream body.
#[tokio::test]
async fn wake_returns_503_on_worker_failure() {
    let mut workers_server = mockito::Server::new_async().await;

    let workers_mock = workers_server
        .mock("GET", "/health")
        .with_status(500)
        .with_body("upstream boom secret detail")
        .expect(1)
        .create_async()
        .await;

    let state = make_state_with_stub_token(&workers_server.url());
    let app = router(state);

    let resp = get_wake(app).await;

    assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "upstream_unavailable");
    assert!(body["request_id"].is_string());
    assert!(
        !body.to_string().contains("boom"),
        "envelope must not leak upstream detail"
    );

    workers_mock.assert_async().await;
}

// ---------------------------------------------------------------------------
// Unreachable worker (connection refused) → 503
// ---------------------------------------------------------------------------

/// Worker host unreachable → gateway returns `503` uniform envelope.
#[tokio::test]
async fn wake_returns_503_when_worker_unreachable() {
    let state = make_state_with_stub_token("http://127.0.0.1:1");
    let app = router(state);

    let resp = get_wake(app).await;

    assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "upstream_unavailable");
    assert!(body["request_id"].is_string());
}

// ---------------------------------------------------------------------------
// ID-token fetch failure → 503
// ---------------------------------------------------------------------------

/// Metadata server returns 500 for the ID-token fetch → gateway returns `503`.
#[tokio::test]
async fn wake_returns_503_when_id_token_fetch_fails() {
    let mut meta_server = mockito::Server::new_async().await;

    let audience = "http://test-workers-wake.invalid".to_string();
    let _meta_mock = meta_server
        .mock(
            "GET",
            "/computeMetadata/v1/instance/service-accounts/default/identity",
        )
        .match_query(Matcher::UrlEncoded("audience".into(), audience.clone()))
        .with_status(500)
        .with_body("internal error")
        .create_async()
        .await;

    let id_token_provider = Arc::new(
        IdTokenProvider::with_base_url_and_audience(meta_server.url(), audience.clone()).unwrap(),
    );
    let mut config = make_test_config(&audience);
    config.workers_url = audience.clone();
    let state = Arc::new(AppState::new_with_id_token_provider(config, id_token_provider).unwrap());
    let app = router(state);

    let resp = get_wake(app).await;

    assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "upstream_unavailable");
}
