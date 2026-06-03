//! Integration tests for the gateway `/healthz` + `/health` endpoints (FOUND-001).
//!
//! The aggregate probes the workers tier, which on Cloud Run requires an
//! IAM-signed ID token. These tests inject a stub `IdTokenProvider` so the
//! probe carries a deterministic bearer without a live metadata server.

#![allow(clippy::unwrap_used)]

mod common;
use common::jwt_helpers::{make_app_state, make_test_config};

use archiviste_gateway::{auth_metadata::IdTokenProvider, router, state::AppState};
use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use mockito::Matcher;
use std::sync::Arc;
use tower::ServiceExt;

/// Stub bearer minted by `IdTokenProvider::new_stub_always_valid` (see `id_token.rs`).
const STUB_BEARER: &str = "stub-id-token-for-tests";

async fn body_json(resp: axum::response::Response) -> serde_json::Value {
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap()
}

async fn get(app: axum::Router, uri: &str) -> axum::response::Response {
    app.oneshot(Request::builder().uri(uri).body(Body::empty()).unwrap())
        .await
        .unwrap()
}

/// AC-4, AC-5: GET /healthz returns 200 + `{status, version}` JSON. When the
/// workers tier is unreachable (token fetch succeeds via stub, but the workers
/// call fails), status degrades to `"degraded"` while still returning HTTP 200.
#[tokio::test]
async fn healthz_returns_degraded_when_workers_unreachable() {
    let state = make_app_state("http://127.0.0.1:1");
    let response = get(router(state), "/healthz").await;

    assert_eq!(response.status(), StatusCode::OK);
    let body = body_json(response).await;
    assert_eq!(body["status"], "degraded");
    assert_eq!(body["version"], "0.1.0");
}

/// `/health` aliases `/healthz` (same handler). The alias exists because Cloud Run's
/// public frontend reserves the literal `/healthz` path and 404s it before the
/// container sees it; the Deploy smoke test probes `/health`.
#[tokio::test]
async fn health_alias_matches_healthz() {
    let state = make_app_state("http://127.0.0.1:1");
    let response = get(router(state), "/health").await;

    assert_eq!(response.status(), StatusCode::OK);
    let body = body_json(response).await;
    assert_eq!(body["status"], "degraded");
    assert_eq!(body["version"], "0.1.0");
}

/// Authenticated-ok path: a workers mock returning 200 → status `"ok"`, and the
/// outbound probe must carry `Authorization: Bearer <id-token>` exactly like the
/// chat path. This is the case the Deploy canary smoke gate asserts (`.status == "ok"`).
#[tokio::test]
async fn health_returns_ok_when_workers_authenticated() {
    let mut workers_server = mockito::Server::new_async().await;

    let workers_mock = workers_server
        .mock("GET", "/health")
        .match_header(
            "Authorization",
            Matcher::Exact(format!("Bearer {STUB_BEARER}")),
        )
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"status":"ok"}"#)
        .expect(1)
        .create_async()
        .await;

    let state = make_app_state(&workers_server.url());
    let response = get(router(state), "/health").await;

    assert_eq!(response.status(), StatusCode::OK);
    let body = body_json(response).await;
    assert_eq!(body["status"], "ok");
    assert_eq!(body["version"], "0.1.0");

    workers_mock.assert_async().await;
}

/// When the workers tier rejects the probe (401, e.g. missing/invalid IAM token),
/// the aggregate degrades while still returning HTTP 200.
#[tokio::test]
async fn health_returns_degraded_when_workers_rejects() {
    let mut workers_server = mockito::Server::new_async().await;

    let workers_mock = workers_server
        .mock("GET", "/health")
        .with_status(401)
        .with_body(r#"{"error":"unauthorized"}"#)
        .expect(1)
        .create_async()
        .await;

    let state = make_app_state(&workers_server.url());
    let response = get(router(state), "/health").await;

    assert_eq!(response.status(), StatusCode::OK);
    let body = body_json(response).await;
    assert_eq!(body["status"], "degraded");

    workers_mock.assert_async().await;
}

/// Token fetch failure must degrade (never 500): an `IdTokenProvider` pointed at
/// a metadata server that 500s yields `"degraded"` with HTTP 200, and the
/// workers tier is never contacted.
#[tokio::test]
async fn health_returns_degraded_when_token_fetch_fails() {
    let mut meta_server = mockito::Server::new_async().await;
    let workers_url = "http://127.0.0.1:1".to_string();

    let meta_mock = meta_server
        .mock(
            "GET",
            "/computeMetadata/v1/instance/service-accounts/default/identity",
        )
        .match_query(Matcher::UrlEncoded("audience".into(), workers_url.clone()))
        .with_status(500)
        .with_body("internal error")
        .create_async()
        .await;

    let id_token_provider = Arc::new(
        IdTokenProvider::with_base_url_and_audience(meta_server.url(), workers_url.clone())
            .unwrap(),
    );
    let mut config = make_test_config(&workers_url);
    config.workers_url = workers_url;
    let state = Arc::new(AppState::new_with_id_token_provider(config, id_token_provider).unwrap());

    let response = get(router(state), "/health").await;

    assert_eq!(response.status(), StatusCode::OK);
    let body = body_json(response).await;
    assert_eq!(body["status"], "degraded");

    meta_mock.assert_async().await;
}
