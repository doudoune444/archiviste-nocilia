//! Integration tests for #295 — "réveil en cours" indicator on a slow
//! `POST /v1/chat` (cold worker wakeup).
//!
//! The frontend is vanilla JS served by the gateway (ADR-0005); there is no JS
//! test runner (adding one needs an ADR), so behaviour is asserted on the served
//! `app.js` asset, mirroring `citations_frontend_test.rs` and `static_test.rs`.
//!
//! Covers #295 acceptance criteria:
//! - A `POST /v1/chat` in flight beyond ~3 s shows a "réveil en cours" message.
//! - The message is removed as soon as the response arrives (success or error).
//! - No automatic double-send; the send-button state cycle is unchanged.

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

/// A slow in-flight chat POST schedules the wakeup notice after a short delay
/// (~3 s) rather than showing it immediately.
#[tokio::test]
async fn slow_chat_schedules_wakeup_notice_after_delay() {
    let js = app_js().await;
    assert!(
        js.contains("showWakeupNotice"),
        "app.js must define a wakeup-notice renderer (showWakeupNotice)"
    );
    assert!(
        js.contains("WAKEUP_DELAY_MS = 3000"),
        "app.js must arm the wakeup notice after a ~3 s delay (WAKEUP_DELAY_MS = 3000)"
    );
    assert!(
        js.contains("setTimeout(showWakeupNotice, WAKEUP_DELAY_MS)"),
        "app.js must schedule showWakeupNotice via setTimeout with the wakeup delay"
    );
    assert!(
        js.contains("réveille"),
        "the wakeup notice text must reassure the visitor the service is waking up"
    );
}

/// The notice is cleared on the response — both the pending timer and any
/// rendered notice are removed when the request settles (success or error).
#[tokio::test]
async fn wakeup_notice_cleared_when_response_arrives() {
    let js = app_js().await;
    assert!(
        js.contains("clearWakeupNotice"),
        "app.js must define a teardown that removes the notice (clearWakeupNotice)"
    );
    assert!(
        js.contains("clearTimeout(wakeupTimer)"),
        "clearWakeupNotice must cancel the pending wakeup timer so a settled request shows no stale notice"
    );
    assert!(
        js.contains("finally"),
        "the chat request must clear the wakeup notice in a finally block so success and error both tear it down"
    );
}

/// No automatic double-send is introduced: the wakeup path adds no extra
/// fetch("/v1/chat") call beyond the single in-flight request.
#[tokio::test]
async fn wakeup_path_introduces_no_duplicate_send() {
    let js = app_js().await;
    let chat_post_calls = js.matches("fetch(\"/v1/chat\"").count();
    assert_eq!(
        chat_post_calls, 1,
        "the wakeup indicator must not introduce a second fetch(\"/v1/chat\") — exactly one in-flight request"
    );
}

/// The notice is rendered as inert text (`textContent`), never `innerHTML`, so
/// no markup can be injected through the wakeup path (security.md).
#[tokio::test]
async fn wakeup_notice_uses_textcontent_only() {
    let js = app_js().await;
    assert!(
        !js.contains(".innerHTML"),
        "app.js must never use the .innerHTML sink — use textContent"
    );
}
