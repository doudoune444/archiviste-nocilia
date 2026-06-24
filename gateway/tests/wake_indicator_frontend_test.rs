//! Integration tests for #295 — "réveil en cours" indicator on a slow `/v1/chat`.
//!
//! The frontend is vanilla JS served by the gateway (ADR-0005); there is no JS
//! test runner (adding one needs an ADR), so behaviour is asserted on the served
//! `app.js` asset (Seam 2), mirroring `citations_frontend_test.rs` and
//! `static_test.rs`.
//!
//! Covers #295 AC:
//! - A `POST /v1/chat` in flight beyond ~3 s renders a "réveil en cours" message.
//! - The message is removed as soon as the response arrives (success or error).
//! - No automatic double-send; the send button state cycle is unchanged.

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

/// A slow in-flight `/v1/chat` arms a timer (~3 s) that renders the wake message.
#[tokio::test]
async fn arms_wake_timer_on_slow_chat() {
    let js = app_js().await;
    assert!(
        js.contains("showWakeIndicator"),
        "app.js must define a wake-indicator renderer (showWakeIndicator)"
    );
    assert!(
        js.contains("WAKE_INDICATOR_DELAY_MS"),
        "app.js must arm the wake message behind a named delay (WAKE_INDICATOR_DELAY_MS)"
    );
    assert!(js.contains("3000"), "the wake delay must be ~3 s (3000 ms)");
    assert!(
        js.contains("setTimeout(showWakeIndicator, WAKE_INDICATOR_DELAY_MS)"),
        "app.js must arm showWakeIndicator behind the delay during the in-flight POST"
    );
}

/// The wake message text reassures the visitor that the service is waking up.
#[tokio::test]
async fn wake_message_reassures_about_wakeup() {
    let js = app_js().await;
    assert!(
        js.contains("Le service se réveille"),
        "app.js must show a reassuring \"Le service se réveille…\" wake message"
    );
}

/// The indicator is removed as soon as the response arrives (success or error):
/// the timer is cleared and the message hidden in a `finally`-style cleanup.
#[tokio::test]
async fn clears_wake_indicator_when_response_arrives() {
    let js = app_js().await;
    assert!(
        js.contains("clearTimeout(wakeTimer)"),
        "app.js must clear the armed wake timer once the response arrives"
    );
    assert!(
        js.contains("hideWakeIndicator"),
        "app.js must define a hide path that removes the wake message"
    );
    assert!(
        js.contains("} finally {"),
        "wake cleanup must run on both success and error (finally block)"
    );
}

/// The wake message is rendered as inert text (textContent, never innerHTML) so
/// the indicator path introduces no markup-injection sink (security.md).
#[tokio::test]
async fn wake_indicator_uses_textcontent_not_innerhtml() {
    let js = app_js().await;
    assert!(
        !js.contains(".innerHTML"),
        "app.js must never use the .innerHTML sink — use textContent"
    );
}

/// No automatic double-send: the single in-flight request keeps the existing
/// button-state cycle (disabled before, re-enabled after) — no retry/resend.
#[tokio::test]
async fn keeps_single_inflight_request_no_double_send() {
    let js = app_js().await;
    assert!(
        js.contains("sendBtn.disabled = true;"),
        "send button must still be disabled while the request is in flight"
    );
    assert!(
        js.contains("sendBtn.disabled = false;"),
        "send button must still be re-enabled after the response"
    );
    // A re-send would issue a second POST /v1/chat from the submit path — there
    // must be exactly one such fetch call in the submit handler.
    let occurrences = js.matches("fetch(\"/v1/chat\"").count();
    assert_eq!(
        occurrences, 1,
        "submit path must issue exactly one POST /v1/chat — no automatic resend"
    );
}
