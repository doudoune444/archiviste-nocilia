//! Integration tests for CIT-001 — "Sources" list rendered under each answer.
//!
//! The frontend is vanilla JS served by the gateway (ADR-0005); there is no JS
//! test runner (adding one needs an ADR), so behaviour is asserted on the served
//! `app.js` asset and the static `index.html`, mirroring `static_test.rs` and
//! `test_dashboard_frontend.rs`.
//!
//! Covers CIT-001 AC-1..AC-4:
//! - AC-1: a "Sources" list is rendered from the response `citations`.
//! - AC-2: only response `citations` (`source_path`) are shown — no fabrication.
//! - AC-3: sources rendered as safe text (`textContent`, never `innerHTML`).
//! - AC-4: no "Sources" block when `citations` is empty/absent (off-topic /
//!   lore-gap / mystery).

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

/// AC-1/AC-2: the served `app.js` builds a Sources list from the response
/// `citations` payload (`source_path`), gated behind a renderer entry point.
#[tokio::test]
async fn ac1_app_js_renders_sources_from_citations() {
    let app = router(make_app_state("http://127.0.0.1:1"));
    let resp = get(app, "/assets/app.js").await;
    assert_eq!(resp.status(), StatusCode::OK);

    let js = body_string(resp).await;
    assert!(
        js.contains("appendSources"),
        "app.js must define a Sources renderer (appendSources)"
    );
    assert!(
        js.contains("appendSources(body.citations)"),
        "app.js must feed the response `citations` payload into the renderer"
    );
    assert!(
        js.contains("source_path"),
        "app.js must read each citation's source_path"
    );
    assert!(
        js.contains("\"Sources\""),
        "app.js must label the list \"Sources\""
    );
}

/// AC-3: sources are rendered as inert text — `app.js` uses `textContent` and
/// never `innerHTML`, so a poisoned `source_path` cannot inject markup.
#[tokio::test]
async fn ac3_app_js_never_uses_innerhtml() {
    let app = router(make_app_state("http://127.0.0.1:1"));
    let resp = get(app, "/assets/app.js").await;
    assert_eq!(resp.status(), StatusCode::OK);

    let js = body_string(resp).await;
    // Match the DOM sink (`.innerHTML` access/assignment), not the bare word —
    // which legitimately appears in "(never innerHTML)" explanatory comments.
    assert!(
        !js.contains(".innerHTML"),
        "app.js must never use the .innerHTML sink (markup injection risk) — use textContent"
    );
    assert!(
        js.contains("item.textContent = citation.source_path"),
        "each source_path must be written via textContent"
    );
}

/// AC-4: the renderer short-circuits on an empty/absent `citations` array, so
/// off-topic / lore-gap / mystery answers show no "Sources" block.
#[tokio::test]
async fn ac4_app_js_guards_empty_citations() {
    let app = router(make_app_state("http://127.0.0.1:1"));
    let resp = get(app, "/assets/app.js").await;
    assert_eq!(resp.status(), StatusCode::OK);

    let js = body_string(resp).await;
    assert!(
        js.contains("!Array.isArray(citations) || citations.length === 0"),
        "appendSources must return early when citations is empty or absent"
    );
}

/// AC-3 (defence in depth): `index.html` carries no inline script/style/handlers
/// so the sources markup can only come from the CSP-allowed `app.js`.
#[test]
fn ac3_index_html_has_no_inline() {
    let html = std::fs::read_to_string("static/index.html")
        .expect("static/index.html not found — check CWD or file creation");

    assert!(
        !html.contains("<script>") && !html.contains("<script\n"),
        "index.html must not contain an inline <script> block"
    );
    assert!(
        !html.contains("<style"),
        "index.html must not contain a <style> block"
    );
    assert!(
        !html.contains("style=\"") && !html.contains("style='"),
        "index.html must not contain a style= attribute"
    );
}
