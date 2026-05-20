//! Integration tests for the gateway `/healthz` endpoint (FOUND-001).

#![allow(clippy::unwrap_used)]

mod common;
use common::jwt_helpers::test_public_key_pem;

use archiviste_gateway::{config::Config, router, state::AppState};
use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use std::sync::Arc;
use tower::ServiceExt;

/// AC-4, AC-5: GET /healthz returns 200 + `{status, version}` JSON. The
/// handler attempts to reach the workers tier; when unreachable, status
/// degrades to `"degraded"` while still returning HTTP 200.
#[tokio::test]
async fn healthz_returns_degraded_when_workers_unreachable() {
    let config = Config {
        bind_addr: "127.0.0.1:0".to_string(),
        workers_url: "http://127.0.0.1:1".to_string(),
        database_url: "postgres://test".to_string(),
        jwt_ed25519_public_key_pem: test_public_key_pem().to_string(),
        version: "0.1.0".to_string(),
        connect_timeout_ms: 500,
        request_timeout_ms: 1_000,
    };
    let state = Arc::new(AppState::new(config).unwrap());
    let app = router(state);

    let response = app
        .oneshot(
            Request::builder()
                .uri("/healthz")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(response.status(), StatusCode::OK);

    let bytes = response.into_body().collect().await.unwrap().to_bytes();
    let body: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
    assert_eq!(body["status"], "degraded");
    assert_eq!(body["version"], "0.1.0");
}
