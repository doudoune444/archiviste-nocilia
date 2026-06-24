//! Integration tests for #296 — fire-and-forget worker pre-warm on the first
//! focus of the query input (once per session).
//!
//! The frontend is vanilla JS served by the gateway (ADR-0005); there is no JS
//! test runner (adding one needs an ADR), so behaviour is asserted on the served
//! `app.js` asset, mirroring `wakeup_indicator_frontend_test.rs` and
//! `citations_frontend_test.rs`.
//!
//! Covers #296 acceptance criteria:
//! - The first `focus` on `#query-input` triggers `fetch("/v1/wake")`
//!   fire-and-forget.
//! - A session flag guarantees a single trigger per session (later focuses are
//!   no-ops).
//! - No pre-warm on page load.

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

async fn app_js() -> String {
    let app = router(make_app_state("http://127.0.0.1:1"));
    let resp = get(app, "/assets/app.js").await;
    assert_eq!(resp.status(), StatusCode::OK);
    body_string(resp).await
}

/// The first focus on the query input fires the pre-warm: a `focus` listener on
/// `#query-input` issues `fetch("/v1/wake")`.
#[tokio::test]
async fn first_focus_triggers_wake_fetch() {
    let js = app_js().await;
    assert!(
        js.contains("getElementById(\"query-input\")"),
        "app.js must reach the query input to arm the pre-warm on focus"
    );
    assert!(
        js.contains("\"focus\""),
        "app.js must register a focus listener to trigger the pre-warm"
    );
    assert!(
        js.contains("fetch(\"/v1/wake\")"),
        "the first focus must fire-and-forget a fetch(\"/v1/wake\")"
    );
}

/// A session flag guards the pre-warm so it runs at most once per session;
/// subsequent focuses are no-ops.
#[tokio::test]
async fn prewarm_is_guarded_by_a_session_flag() {
    let js = app_js().await;
    assert!(
        js.contains("prewarmTriggered"),
        "app.js must hold a session flag (prewarmTriggered) so the pre-warm runs only once"
    );
    assert!(
        js.contains("if (prewarmTriggered)"),
        "the focus handler must short-circuit when the session flag is already set"
    );
}

/// The pre-warm is wired to focus only — it must not be fired at module load /
/// page load (which would wake the worker for every visitor).
#[tokio::test]
async fn no_prewarm_on_page_load() {
    let js = app_js().await;
    let wake_fetch_calls = js.matches("fetch(\"/v1/wake\")").count();
    assert_eq!(
        wake_fetch_calls, 1,
        "exactly one fetch(\"/v1/wake\") call site — the pre-warm, reached only via the focus handler"
    );
}
