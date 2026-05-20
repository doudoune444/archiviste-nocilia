//! Integration tests for static file serving and security headers (UI-001 / SEC-003).
//!
//! AC references per criterion are noted inline.

#![allow(clippy::unwrap_used)]
#![allow(clippy::expect_used)]

mod common;
use common::jwt_helpers::test_public_key_pem;

use archiviste_gateway::{config::Config, router, state::AppState};
use axum::body::Body;
use axum::http::{Request, StatusCode};
use http_body_util::BodyExt;
use std::sync::Arc;
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn make_state() -> Arc<AppState> {
    let config = Config {
        bind_addr: "127.0.0.1:0".to_string(),
        workers_url: "http://127.0.0.1:1".to_string(),
        database_url: "postgres://test".to_string(),
        jwt_ed25519_public_key_pem: test_public_key_pem().to_string(),
        version: "0.1.0".to_string(),
        connect_timeout_ms: 500,
        request_timeout_ms: 35_000,
    };
    Arc::new(AppState::new(config).unwrap())
}

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

/// Expected value of the Content-Security-Policy header (AC-6 literal).
const CSP_VALUE: &str = "default-src 'self'; script-src 'self'; style-src 'self'; \
    img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; \
    base-uri 'none'; form-action 'self'";

/// Expected value of the Strict-Transport-Security header (SEC-003 AC-1 / AC-8 literal).
const HSTS_VALUE: &str = "max-age=31536000; includeSubDomains; preload";

/// Assert all 5 security headers are present with exact literal values (AC-2, AC-8).
///
/// Also asserts each header name appears exactly once — no duplicates (AC-6).
fn assert_security_headers(resp: &axum::response::Response) {
    let headers = resp.headers();

    // AC-1 / SEC-003: HSTS header.
    let hsts = headers
        .get("strict-transport-security")
        .expect("Strict-Transport-Security header missing")
        .to_str()
        .unwrap();
    assert_eq!(hsts, HSTS_VALUE, "HSTS value mismatch");

    let csp = headers
        .get("content-security-policy")
        .expect("CSP header missing")
        .to_str()
        .unwrap();
    assert_eq!(csp, CSP_VALUE, "CSP value mismatch");

    let xcto = headers
        .get("x-content-type-options")
        .expect("X-Content-Type-Options header missing")
        .to_str()
        .unwrap();
    assert_eq!(xcto, "nosniff", "X-Content-Type-Options mismatch");

    let referrer = headers
        .get("referrer-policy")
        .expect("Referrer-Policy header missing")
        .to_str()
        .unwrap();
    assert_eq!(
        referrer, "strict-origin-when-cross-origin",
        "Referrer-Policy mismatch"
    );

    let xfo = headers
        .get("x-frame-options")
        .expect("X-Frame-Options header missing")
        .to_str()
        .unwrap();
    assert_eq!(xfo, "DENY", "X-Frame-Options mismatch");

    // AC-6: no header duplicated — each of the 5 names must appear exactly once.
    for name in &[
        "strict-transport-security",
        "content-security-policy",
        "x-content-type-options",
        "referrer-policy",
        "x-frame-options",
    ] {
        let count = headers.get_all(*name).iter().count();
        assert_eq!(
            count, 1,
            "header '{name}' appears {count} times, expected 1"
        );
    }
}

// ---------------------------------------------------------------------------
// AC-1 : GET / returns 200 + text/html + stable selector
// ---------------------------------------------------------------------------

/// AC-1: GET / → 200, Content-Type text/html; charset=utf-8, body has <form id="chat-form".
#[tokio::test]
async fn ac1_get_root_returns_html() {
    let app = router(make_state());
    let resp = get(app, "/").await;

    assert_eq!(resp.status(), StatusCode::OK);

    let ct = resp
        .headers()
        .get("content-type")
        .expect("content-type missing")
        .to_str()
        .unwrap();
    assert!(ct.starts_with("text/html"), "expected text/html, got: {ct}");

    let body = body_string(resp).await;
    assert!(
        body.contains(r#"<form id="chat-form""#),
        "stable selector <form id=\"chat-form\" not found in body"
    );
}

// ---------------------------------------------------------------------------
// AC-2 : GET /assets/app.js returns 200 + JS content-type
// ---------------------------------------------------------------------------

/// AC-2: GET /assets/app.js → 200, Content-Type application/javascript or text/javascript.
#[tokio::test]
async fn ac2_get_app_js_returns_javascript() {
    let app = router(make_state());
    let resp = get(app, "/assets/app.js").await;

    assert_eq!(resp.status(), StatusCode::OK);

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

// ---------------------------------------------------------------------------
// AC-3 : GET /assets/styles.css returns 200 + text/css
// ---------------------------------------------------------------------------

/// AC-3: GET /assets/styles.css → 200, Content-Type text/css.
#[tokio::test]
async fn ac3_get_styles_css_returns_css() {
    let app = router(make_state());
    let resp = get(app, "/assets/styles.css").await;

    assert_eq!(resp.status(), StatusCode::OK);

    let ct = resp
        .headers()
        .get("content-type")
        .expect("content-type missing")
        .to_str()
        .unwrap();
    assert!(ct.starts_with("text/css"), "expected text/css, got: {ct}");
}

// ---------------------------------------------------------------------------
// AC-4 : GET /assets/inexistant.txt → 404
// ---------------------------------------------------------------------------

/// AC-4: unknown asset path → 404.
#[tokio::test]
async fn ac4_unknown_asset_returns_404() {
    let app = router(make_state());
    let resp = get(app, "/assets/inexistant.txt").await;
    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
}

// ---------------------------------------------------------------------------
// AC-5 : path traversal → 400 or 404
// ---------------------------------------------------------------------------

/// AC-5: /assets/../Cargo.toml → 400 or 404, body must not contain Cargo content.
#[tokio::test]
async fn ac5_path_traversal_blocked() {
    let app = router(make_state());
    let resp = get(app, "/assets/../Cargo.toml").await;

    let status = resp.status();
    assert!(
        status == StatusCode::NOT_FOUND || status == StatusCode::BAD_REQUEST,
        "expected 400 or 404 for path traversal, got: {status}"
    );

    let body = body_string(resp).await;
    assert!(
        !body.contains("[package]"),
        "path traversal leaked Cargo.toml contents"
    );
}

/// AC-5: percent-encoded traversal %2e%2e → 400 or 404.
#[tokio::test]
async fn ac5_percent_encoded_traversal_blocked() {
    let app = router(make_state());
    let resp = get(app, "/assets/%2e%2e/Cargo.toml").await;

    let status = resp.status();
    assert!(
        status == StatusCode::NOT_FOUND || status == StatusCode::BAD_REQUEST,
        "expected 400 or 404 for encoded traversal, got: {status}"
    );
}

// ---------------------------------------------------------------------------
// AC-6 : security headers on static routes
// ---------------------------------------------------------------------------

/// AC-6: GET / has all 4 security headers with exact literal values.
#[tokio::test]
async fn ac6_root_has_security_headers() {
    let app = router(make_state());
    let resp = get(app, "/").await;
    assert_eq!(resp.status(), StatusCode::OK);
    assert_security_headers(&resp);
}

/// AC-6: GET /assets/app.js has all 4 security headers.
#[tokio::test]
async fn ac6_app_js_has_security_headers() {
    let app = router(make_state());
    let resp = get(app, "/assets/app.js").await;
    assert_eq!(resp.status(), StatusCode::OK);
    assert_security_headers(&resp);
}

/// AC-6: GET /assets/styles.css has all 4 security headers.
#[tokio::test]
async fn ac6_styles_css_has_security_headers() {
    let app = router(make_state());
    let resp = get(app, "/assets/styles.css").await;
    assert_eq!(resp.status(), StatusCode::OK);
    assert_security_headers(&resp);
}

// ---------------------------------------------------------------------------
// AC-7 : no inline script/style/on* in index.html (static grep)
// ---------------------------------------------------------------------------

/// AC-7: index.html contains no inline <script> content, <style> blocks,
/// style="..." attributes, or on*= event handlers.
#[test]
fn ac7_no_inline_in_index_html() {
    // Path relative to gateway/ CWD (cargo test runs from gateway/).
    let html = std::fs::read_to_string("static/index.html")
        .expect("static/index.html not found — check CWD or file creation");

    // No inline script content (script tag with body content).
    assert!(
        !html.contains("<script>") && !html.contains("<script\n"),
        "index.html must not contain inline <script> block"
    );

    // No inline style block.
    assert!(
        !html.contains("<style"),
        "index.html must not contain <style> block"
    );

    // No style= attribute.
    assert!(
        !html.contains("style=\"") && !html.contains("style='"),
        "index.html must not contain style=\"...\" attribute"
    );

    // No inline event handlers (on + lowercase letter + =).
    let lower = html.to_lowercase();
    let has_on_handler = lower.contains(" onclick=")
        || lower.contains(" onload=")
        || lower.contains(" onsubmit=")
        || lower.contains(" onchange=")
        || lower.contains(" oninput=")
        || lower.contains(" onkeydown=")
        || lower.contains(" onkeyup=")
        || lower.contains(" onkeypress=")
        || lower.contains(" onmouseover=")
        || lower.contains(" onfocus=")
        || lower.contains(" onblur=");
    assert!(
        !has_on_handler,
        "index.html must not contain inline on*= event handlers"
    );
}

// ---------------------------------------------------------------------------
// AC-17 : POST /v1/chat also carries security headers (router-level)
// ---------------------------------------------------------------------------

/// AC-17: POST /v1/chat response has all 5 security headers (router-level middleware).
#[tokio::test]
async fn ac17_chat_endpoint_has_security_headers() {
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("POST", "/v1/generate")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"answer":"ok","citations":[]}"#)
        .create_async()
        .await;

    let config = Config {
        bind_addr: "127.0.0.1:0".to_string(),
        workers_url: server.url(),
        database_url: "postgres://test".to_string(),
        jwt_ed25519_public_key_pem: test_public_key_pem().to_string(),
        version: "0.1.0".to_string(),
        connect_timeout_ms: 500,
        request_timeout_ms: 35_000,
    };
    let state = Arc::new(AppState::new(config).unwrap());
    let app = router(state);

    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/chat")
                .header("content-type", "application/json")
                .body(Body::from(r#"{"query":"hello"}"#))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(resp.status(), StatusCode::OK);
    assert_security_headers(&resp);
}

// ---------------------------------------------------------------------------
// SEC-003 AC-3 : GET /healthz carries all 5 security headers
// ---------------------------------------------------------------------------

/// SEC-003 AC-3: GET /healthz 200 (workers up via mockito) has all 5 security headers.
#[tokio::test]
async fn sec003_healthz_200_has_headers() {
    let mut server = mockito::Server::new_async().await;
    let _mock = server
        .mock("GET", "/v1/health")
        .with_status(200)
        .with_header("content-type", "application/json")
        .with_body(r#"{"status":"ok"}"#)
        .create_async()
        .await;

    let config = Config {
        bind_addr: "127.0.0.1:0".to_string(),
        workers_url: server.url(),
        database_url: "postgres://test".to_string(),
        jwt_ed25519_public_key_pem: test_public_key_pem().to_string(),
        version: "0.1.0".to_string(),
        connect_timeout_ms: 500,
        request_timeout_ms: 35_000,
    };
    let state = Arc::new(AppState::new(config).unwrap());
    let app = router(state);

    let resp = get(app, "/healthz").await;

    assert_eq!(resp.status(), StatusCode::OK);
    assert_security_headers(&resp);
}

/// SEC-003 AC-3: GET /healthz workers-down (degraded 200) has all 5 security headers.
///
/// The healthz handler returns HTTP 200 with `"degraded"` when workers are unreachable
/// (port 1 = always-closed). Headers must still be present on this 200 response.
#[tokio::test]
async fn sec003_healthz_workers_down_has_headers() {
    // workers_url points at closed port — handler returns 200 "degraded".
    let app = router(make_state());
    let resp = get(app, "/healthz").await;

    assert_eq!(resp.status(), StatusCode::OK);
    assert_security_headers(&resp);
}

// ---------------------------------------------------------------------------
// SEC-003 AC-4 : POST /v1/chat body > 1 MiB → 400 carries all 5 security headers
// ---------------------------------------------------------------------------

/// SEC-003 AC-4: oversized body (> 1 MiB) → 400 response carries all 5 security headers.
#[tokio::test]
async fn sec003_chat_body_limit_400_has_headers() {
    // Body of exactly 1 048 577 bytes triggers RequestBodyLimitLayer (1 MiB cap).
    let oversized_body = vec![b'x'; 1_048_577];

    let app = router(make_state());
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/chat")
                .header("content-type", "application/json")
                .body(Body::from(oversized_body))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    assert_security_headers(&resp);
}

// ---------------------------------------------------------------------------
// SEC-003 AC-5 : 404 responses carry all 5 security headers
// ---------------------------------------------------------------------------

/// SEC-003 AC-5: GET /assets/inexistant.txt (`ServeDir` 404) has all 5 security headers.
#[tokio::test]
async fn sec003_unknown_asset_404_has_headers() {
    let app = router(make_state());
    let resp = get(app, "/assets/inexistant.txt").await;

    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    assert_security_headers(&resp);
}

/// SEC-003 AC-5: GET /chemin/inexistant (router fallback 404) has all 5 security headers.
#[tokio::test]
async fn sec003_unknown_route_404_has_headers() {
    let app = router(make_state());
    let resp = get(app, "/chemin/inexistant").await;

    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
    assert_security_headers(&resp);
}
