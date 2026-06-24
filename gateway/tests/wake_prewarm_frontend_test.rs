//! Seam 2 tests for #296 — fire-and-forget worker pre-wake on first input focus.
//!
//! The frontend is vanilla JS served by the gateway (ADR-0005); there is no JS
//! test runner (adding one needs an ADR), so behaviour is asserted on the served
//! `app.js` asset, mirroring `citations_frontend_test.rs` and `static_test.rs`.
//!
//! Covers #296 acceptance criteria:
//! - first `focus` on `#query-input` triggers `fetch("/v1/wake")` fire-and-forget;
//! - a session flag guarantees a single trigger per session (later focuses no-op);
//! - no pre-wake on page load.

#![allow(clippy::unwrap_used)]
#![allow(clippy::expect_used)]

mod common;
use common::jwt_helpers::make_app_state;

use archiviste_gateway::router;
use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use tower::ServiceExt;

async fn get(app: axum::Router, uri: &str) -> axum::response::Response {
    app.oneshot(
        Request::builder()
            .method("GET")
            .uri(uri)
            .body(Body::empty())
            .unwrap(),
    )
    .await
    .unwrap()
}

async fn body_string(resp: axum::response::Response) -> String {
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    String::from_utf8_lossy(&bytes).to_string()
}

async fn served_app_js() -> String {
    let app = router(make_app_state("http://127.0.0.1:1"));
    let resp = get(app, "/assets/app.js").await;
    assert_eq!(resp.status(), StatusCode::OK);
    body_string(resp).await
}

/// AC: the served `app.js` wires the pre-wake on the input `focus` event, and
/// the wake call targets the public `/v1/wake` route.
#[tokio::test]
async fn app_js_wires_prewake_on_input_focus() {
    let js = served_app_js().await;
    assert!(
        js.contains("\"focus\""),
        "app.js must register a focus listener for the pre-wake"
    );
    assert!(
        js.contains("query-input"),
        "app.js must bind the pre-wake to the #query-input field"
    );
    assert!(
        js.contains("fetch(\"/v1/wake\")"),
        "app.js must call fetch(\"/v1/wake\") to pre-wake the worker"
    );
}

/// AC: a session flag guarantees a single pre-wake per session — later focuses
/// are a no-op (the flag is checked then set).
#[tokio::test]
async fn app_js_guards_prewake_with_session_flag() {
    let js = served_app_js().await;
    assert!(
        js.contains("hasPrewarmed"),
        "app.js must keep a session flag (hasPrewarmed) gating the pre-wake"
    );
}
