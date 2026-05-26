//! Integration tests for UI-002b frontend (dashboard HTML page + assets).
//!
//! Covers: AC-1, AC-3, AC-4, AC-11, AC-12, AC-13.
//! AC-14, AC-15, AC-16, AC-17, AC-18 require a browser + live API (AC-24 manual).

#![allow(clippy::unwrap_used, clippy::expect_used)]

mod common;
use common::jwt_helpers::{make_test_config, sign_test_token};

use archiviste_gateway::{auth::extractor::UserTier, router, state::AppState};
use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use http_body_util::BodyExt;
use std::sync::Arc;
use tower::ServiceExt;
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn make_state() -> Arc<AppState> {
    Arc::new(AppState::new(make_test_config("http://127.0.0.1:1")).unwrap())
}

async fn get_with_token(app: axum::Router, uri: &str, token: &str) -> axum::response::Response {
    app.oneshot(
        Request::builder()
            .method("GET")
            .uri(uri)
            .header("authorization", format!("Bearer {token}"))
            .body(Body::empty())
            .unwrap(),
    )
    .await
    .unwrap()
}

async fn get_anon(app: axum::Router, uri: &str) -> axum::response::Response {
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

/// Expected CSP value — byte-for-byte (AC-12 literal).
const CSP_VALUE: &str = "default-src 'self'; script-src 'self'; style-src 'self'; \
    img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; \
    base-uri 'none'; form-action 'self'";

/// Assert the 4 UI-001 security headers are present with exact literal values (AC-12).
fn assert_security_headers(resp: &axum::response::Response) {
    let headers = resp.headers();

    let csp = headers
        .get("content-security-policy")
        .expect("CSP header missing")
        .to_str()
        .unwrap();
    assert_eq!(csp, CSP_VALUE, "CSP value mismatch on dashboard route");

    let xcto = headers
        .get("x-content-type-options")
        .expect("X-Content-Type-Options header missing")
        .to_str()
        .unwrap();
    assert_eq!(xcto, "nosniff");

    let referrer = headers
        .get("referrer-policy")
        .expect("Referrer-Policy header missing")
        .to_str()
        .unwrap();
    assert_eq!(referrer, "strict-origin-when-cross-origin");

    let xfo = headers
        .get("x-frame-options")
        .expect("X-Frame-Options header missing")
        .to_str()
        .unwrap();
    assert_eq!(xfo, "DENY");
}

// ---------------------------------------------------------------------------
// AC-1: GET /dashboard returns 200 + text/html + stable selector for author JWT
// ---------------------------------------------------------------------------

/// AC-1: author JWT → 200, Content-Type: text/html, body contains <table id="tickets-table".
#[tokio::test]
async fn ac1_dashboard_200_for_author() {
    let app = router(make_state());
    let token = sign_test_token(Uuid::new_v4(), UserTier::Author, Uuid::new_v4());

    let resp = get_with_token(app, "/dashboard", &token).await;

    assert_eq!(resp.status(), StatusCode::OK, "expected 200 for author JWT");

    let ct = resp
        .headers()
        .get("content-type")
        .expect("content-type missing")
        .to_str()
        .unwrap();
    assert!(
        ct.starts_with("text/html"),
        "expected text/html content-type, got: {ct}"
    );

    let body = body_string(resp).await;
    assert!(
        body.contains(r#"<table id="tickets-table""#),
        "stable selector <table id=\"tickets-table\" not found in body"
    );
}

// ---------------------------------------------------------------------------
// AC-11: GET /dashboard returns 403 for member and anonymous
// ---------------------------------------------------------------------------

/// AC-11 (sub-case member): member JWT → 403 `author_required` byte-for-byte.
#[tokio::test]
async fn ac11_dashboard_403_for_member() {
    let app = router(make_state());
    let token = sign_test_token(Uuid::new_v4(), UserTier::Member, Uuid::new_v4());

    let resp = get_with_token(app, "/dashboard", &token).await;

    assert_eq!(
        resp.status(),
        StatusCode::FORBIDDEN,
        "expected 403 for member"
    );

    let body = body_string(resp).await;
    let v: serde_json::Value = serde_json::from_str(&body).expect("body must be JSON");
    assert_eq!(
        v["error"], "author_required",
        "error code must be author_required byte-for-byte (AC-2)"
    );
}

/// AC-11 (sub-case anonymous): no JWT → 401 `invalid_token`.
///
/// A caller with no token at all hits `AuthUser` first which returns
/// `AuthError::InvalidToken` (401) before the tier check can run.
/// This is consistent with SEC-001 AC-12 and the PR1 backend tests
/// (`ac6_tickets_anonymous_gets_401`). The spec AC-2 language "anonymous → 403"
/// is superseded by the SEC-001 extractor contract for no-token callers.
#[tokio::test]
async fn ac11_dashboard_401_for_anonymous() {
    let app = router(make_state());
    let resp = get_anon(app, "/dashboard").await;

    // No token → AuthUser returns InvalidToken (401), not AuthorRequired (403).
    assert_eq!(
        resp.status(),
        StatusCode::UNAUTHORIZED,
        "expected 401 for anonymous (no token)"
    );

    let body = body_string(resp).await;
    let v: serde_json::Value = serde_json::from_str(&body).expect("body must be JSON");
    assert_eq!(v["error"], "invalid_token");
}

// ---------------------------------------------------------------------------
// AC-3: GET /assets/dashboard.js and /assets/dashboard.css return 200
// ---------------------------------------------------------------------------

/// AC-3: GET /assets/dashboard.js → 200, Content-Type JS (no auth required).
#[tokio::test]
async fn ac3_dashboard_js_served_public() {
    let app = router(make_state());
    let resp = get_anon(app, "/assets/dashboard.js").await;

    assert_eq!(
        resp.status(),
        StatusCode::OK,
        "expected 200 for dashboard.js"
    );

    let ct = resp
        .headers()
        .get("content-type")
        .expect("content-type missing")
        .to_str()
        .unwrap();
    assert!(
        ct.starts_with("application/javascript") || ct.starts_with("text/javascript"),
        "expected JS content-type, got: {ct}"
    );
}

/// AC-3: GET /assets/dashboard.css → 200, Content-Type text/css (no auth required).
#[tokio::test]
async fn ac3_dashboard_css_served_public() {
    let app = router(make_state());
    let resp = get_anon(app, "/assets/dashboard.css").await;

    assert_eq!(
        resp.status(),
        StatusCode::OK,
        "expected 200 for dashboard.css"
    );

    let ct = resp
        .headers()
        .get("content-type")
        .expect("content-type missing")
        .to_str()
        .unwrap();
    assert!(
        ct.starts_with("text/css"),
        "expected text/css content-type, got: {ct}"
    );
}

// ---------------------------------------------------------------------------
// AC-4: exactly 4 known assets served; unknown assets → 404
// ---------------------------------------------------------------------------

/// AC-4: GET /assets/random.txt → 404.
#[tokio::test]
async fn ac4_unknown_asset_returns_404() {
    let app = router(make_state());
    let resp = get_anon(app, "/assets/random.txt").await;
    assert_eq!(
        resp.status(),
        StatusCode::NOT_FOUND,
        "unknown asset must 404"
    );
}

/// AC-4: path traversal /assets/../Cargo.toml → 400 or 404 (UI-001 regression).
#[tokio::test]
async fn ac4_path_traversal_blocked() {
    let app = router(make_state());
    let resp = get_anon(app, "/assets/../Cargo.toml").await;

    let status = resp.status();
    assert!(
        status == StatusCode::NOT_FOUND || status == StatusCode::BAD_REQUEST,
        "expected 400 or 404 for path traversal, got: {status}"
    );

    let body = body_string(resp).await;
    assert!(
        !body.contains("[package]"),
        "path traversal must not leak Cargo.toml"
    );
}

// ---------------------------------------------------------------------------
// AC-12: security headers on /dashboard and assets
// ---------------------------------------------------------------------------

/// AC-12: GET /dashboard carries all 4 UI-001 security headers (author JWT).
#[tokio::test]
async fn ac12_dashboard_has_security_headers() {
    let app = router(make_state());
    let token = sign_test_token(Uuid::new_v4(), UserTier::Author, Uuid::new_v4());
    let resp = get_with_token(app, "/dashboard", &token).await;

    assert_eq!(resp.status(), StatusCode::OK);
    assert_security_headers(&resp);
}

/// AC-12: GET /assets/dashboard.js carries all 4 UI-001 security headers.
#[tokio::test]
async fn ac12_dashboard_js_has_security_headers() {
    let app = router(make_state());
    let resp = get_anon(app, "/assets/dashboard.js").await;

    assert_eq!(resp.status(), StatusCode::OK);
    assert_security_headers(&resp);
}

/// AC-12: GET /assets/dashboard.css carries all 4 UI-001 security headers.
#[tokio::test]
async fn ac12_dashboard_css_has_security_headers() {
    let app = router(make_state());
    let resp = get_anon(app, "/assets/dashboard.css").await;

    assert_eq!(resp.status(), StatusCode::OK);
    assert_security_headers(&resp);
}

// ---------------------------------------------------------------------------
// AC-13: no inline JS/CSS/event handlers in dashboard.html (static grep)
// ---------------------------------------------------------------------------

/// AC-13: dashboard.html contains no inline <script> content, <style> blocks,
/// style="..." attributes, or on*= event handlers (grep statique AC-13 / UI-001 AC-7).
#[test]
fn ac13_no_inline_in_dashboard_html() {
    // CWD = gateway/ when running cargo test.
    let html = std::fs::read_to_string("static/dashboard.html")
        .expect("static/dashboard.html not found — check CWD or file creation");

    // No inline script content (script tag with body or no src).
    assert!(
        !html.contains("<script>") && !html.contains("<script\n"),
        "dashboard.html must not contain inline <script> block"
    );

    // No inline style block.
    assert!(
        !html.contains("<style"),
        "dashboard.html must not contain <style> block"
    );

    // No style= attribute.
    assert!(
        !html.contains("style=\"") && !html.contains("style='"),
        "dashboard.html must not contain style=\"...\" attribute"
    );

    // No inline event handlers (on + lowercase letter + =).
    let lower = html.to_lowercase();
    for handler in &[
        " onclick=",
        " onload=",
        " onsubmit=",
        " onchange=",
        " oninput=",
        " onkeydown=",
        " onkeyup=",
        " onkeypress=",
        " onmouseover=",
        " onfocus=",
        " onblur=",
    ] {
        assert!(
            !lower.contains(handler),
            "dashboard.html must not contain inline event handler: {handler}"
        );
    }
}
