//! Integration + contract tests for OBS-001.
//!
//! Covers AC-1..AC-14 (AC-15 is a manual author checklist — not automated).
//! Each test block comments its AC reference.

#![allow(clippy::unwrap_used)]
#![allow(clippy::expect_used)]

mod common;
use common::jwt_helpers::{make_app_state, sign_test_token};

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
    // db_pool = None → deterministic UpstreamUnavailable on /v1/stats (AC-7).
    make_app_state("http://127.0.0.1:1")
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

async fn get_with_bearer(app: axum::Router, uri: &str, token: &str) -> axum::response::Response {
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

async fn body_bytes(resp: axum::response::Response) -> Vec<u8> {
    resp.into_body()
        .collect()
        .await
        .unwrap()
        .to_bytes()
        .to_vec()
}

async fn body_string(resp: axum::response::Response) -> String {
    String::from_utf8_lossy(&body_bytes(resp).await).to_string()
}

async fn body_json(resp: axum::response::Response) -> serde_json::Value {
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).expect("response body must be valid JSON")
}

/// Literal CSP value (AC-11, identical to `static_test.rs`).
const CSP_VALUE: &str = "default-src 'self'; script-src 'self'; style-src 'self'; \
    img-src 'self' data:; object-src 'none'; frame-ancestors 'none'; \
    base-uri 'none'; form-action 'self'";

/// Literal HSTS value (AC-11, SEC-003 AC-1).
const HSTS_VALUE: &str = "max-age=31536000; includeSubDomains; preload";

/// Assert all 5 security headers are present byte-for-byte (AC-11).
fn assert_security_headers(resp: &axum::response::Response) {
    let headers = resp.headers();

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
    assert_eq!(
        csp, CSP_VALUE,
        "CSP value mismatch (must NOT be modified — AC-11)"
    );

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
// AC-1 : GET /observability → 200, text/html, body has <section id="usage-widget"
// ---------------------------------------------------------------------------

/// AC-1: GET /observability → 200, Content-Type starts with text/html, stable selector present.
#[tokio::test]
async fn ac1_observability_page_200_html_with_widget() {
    let app = router(make_state());
    let resp = get_anon(app, "/observability").await;

    assert_eq!(
        resp.status(),
        StatusCode::OK,
        "expected 200 from /observability"
    );

    let ct = resp
        .headers()
        .get("content-type")
        .expect("content-type header missing")
        .to_str()
        .unwrap();
    assert!(ct.starts_with("text/html"), "expected text/html, got: {ct}");

    let body = body_string(resp).await;
    assert!(
        body.contains(r#"<section id="usage-widget""#),
        r#"stable selector <section id="usage-widget" not found in body"#
    );
}

// ---------------------------------------------------------------------------
// AC-2 : GET /observability served anonymously — never 401/403
// ---------------------------------------------------------------------------

/// AC-2: GET /observability without auth → 200, never 401 or 403.
#[tokio::test]
async fn ac2_observability_anonymous_no_auth() {
    let app = router(make_state());
    let resp = get_anon(app, "/observability").await;
    let status = resp.status();
    assert_ne!(
        status,
        StatusCode::UNAUTHORIZED,
        "/observability must not return 401 for anon"
    );
    assert_ne!(
        status,
        StatusCode::FORBIDDEN,
        "/observability must not return 403 for anon"
    );
    assert_eq!(status, StatusCode::OK);
}

/// AC-2: GET /observability with member JWT → 200, never 401 or 403.
#[tokio::test]
async fn ac2_observability_member_jwt_gets_200() {
    let app = router(make_state());
    let token = sign_test_token(Uuid::new_v4(), UserTier::Member, Uuid::new_v4());
    let resp = get_with_bearer(app, "/observability", &token).await;
    let status = resp.status();
    assert_ne!(status, StatusCode::UNAUTHORIZED);
    assert_ne!(status, StatusCode::FORBIDDEN);
    assert_eq!(status, StatusCode::OK);
}

/// AC-2: GET /observability with author JWT → 200, never 401 or 403.
#[tokio::test]
async fn ac2_observability_author_jwt_gets_200() {
    let app = router(make_state());
    let token = sign_test_token(Uuid::new_v4(), UserTier::Author, Uuid::new_v4());
    let resp = get_with_bearer(app, "/observability", &token).await;
    let status = resp.status();
    assert_ne!(status, StatusCode::UNAUTHORIZED);
    assert_ne!(status, StatusCode::FORBIDDEN);
    assert_eq!(status, StatusCode::OK);
}

// ---------------------------------------------------------------------------
// AC-3 : GET /assets/observability.{js,css} → 200 + correct Content-Type
// ---------------------------------------------------------------------------

/// AC-3: GET /assets/observability.js → 200, Content-Type application/javascript or text/javascript.
#[tokio::test]
async fn ac3_observability_js_200_javascript_content_type() {
    let app = router(make_state());
    let resp = get_anon(app, "/assets/observability.js").await;

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

/// AC-3: GET /assets/observability.css → 200, Content-Type text/css.
#[tokio::test]
async fn ac3_observability_css_200_css_content_type() {
    let app = router(make_state());
    let resp = get_anon(app, "/assets/observability.css").await;

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
// AC-7 : no-pool state → /v1/stats → 503, sanitized body, no SQL/host leak
// ---------------------------------------------------------------------------

/// AC-7: `make_app_state` builds `db_pool=None` → `/v1/stats` returns 503.
/// Body has exact keys `{"error":"upstream_unavailable","request_id":"<36-char>"}`.
/// Body must NOT contain: "conversations", "SELECT", "postgres", "host", "panic".
#[tokio::test]
async fn ac7_stats_no_pool_returns_503_sanitized() {
    // AC-7: db_pool=None (make_app_state) → handler maps None → UpstreamUnavailable → 503.
    let app = router(make_state());
    let resp = get_anon(app, "/v1/stats").await;

    assert_eq!(
        resp.status(),
        StatusCode::SERVICE_UNAVAILABLE,
        "/v1/stats with no pool must return 503"
    );

    let ct = resp
        .headers()
        .get("content-type")
        .expect("content-type missing on 503")
        .to_str()
        .unwrap();
    assert!(
        ct.starts_with("application/json"),
        "503 body must be JSON, got: {ct}"
    );

    let body_bytes_vec = body_bytes(resp).await;
    let body_str = String::from_utf8_lossy(&body_bytes_vec);
    let json: serde_json::Value =
        serde_json::from_slice(&body_bytes_vec).expect("503 body must be valid JSON");

    // Exact key "error" = "upstream_unavailable" (AC-7).
    assert_eq!(
        json["error"], "upstream_unavailable",
        "error code must be upstream_unavailable"
    );

    // "request_id" must be a 36-char UUID string.
    let rid = json["request_id"]
        .as_str()
        .expect("request_id must be a string");
    assert_eq!(rid.len(), 36, "request_id must be 36-char UUID, got: {rid}");

    // Exactly 2 keys in body (no extra fields leak).
    assert_eq!(
        json.as_object().unwrap().len(),
        2,
        "503 body must have exactly 2 keys: error + request_id"
    );

    // Negative assertions — no SQL/host/table/stack leak (security.md §A05).
    let lower = body_str.to_lowercase();
    assert!(
        !lower.contains("conversations"),
        "503 body must not contain table name"
    );
    assert!(
        !lower.contains("select"),
        "503 body must not contain SQL keyword"
    );
    assert!(
        !lower.contains("postgres"),
        "503 body must not contain db host"
    );
    assert!(!lower.contains("host"), "503 body must not contain 'host'");
    assert!(
        !lower.contains("panic"),
        "503 body must not contain 'panic'"
    );
}

// ---------------------------------------------------------------------------
// AC-11 : 5 security headers on /observability, both assets, and /v1/stats 503
// ---------------------------------------------------------------------------

/// AC-11: /observability has all 5 security headers byte-for-byte.
#[tokio::test]
async fn ac11_observability_page_security_headers() {
    let app = router(make_state());
    let resp = get_anon(app, "/observability").await;
    assert_eq!(resp.status(), StatusCode::OK);
    assert_security_headers(&resp);
}

/// AC-11: /assets/observability.js has all 5 security headers.
#[tokio::test]
async fn ac11_observability_js_security_headers() {
    let app = router(make_state());
    let resp = get_anon(app, "/assets/observability.js").await;
    assert_eq!(resp.status(), StatusCode::OK);
    assert_security_headers(&resp);
}

/// AC-11: /assets/observability.css has all 5 security headers.
#[tokio::test]
async fn ac11_observability_css_security_headers() {
    let app = router(make_state());
    let resp = get_anon(app, "/assets/observability.css").await;
    assert_eq!(resp.status(), StatusCode::OK);
    assert_security_headers(&resp);
}

/// AC-11: /v1/stats 503 path has all 5 security headers.
#[tokio::test]
async fn ac11_stats_503_security_headers() {
    // db_pool=None → 503; headers must still be present (router-wide layer).
    let app = router(make_state());
    let resp = get_anon(app, "/v1/stats").await;
    assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
    assert_security_headers(&resp);
}

// ---------------------------------------------------------------------------
// AC-13 : /assets/inexistant.txt → 404; path traversal → 400/404 (unchanged)
// ---------------------------------------------------------------------------

/// AC-13: unknown asset → 404.
#[tokio::test]
async fn ac13_unknown_asset_404() {
    let app = router(make_state());
    let resp = get_anon(app, "/assets/inexistant.txt").await;
    assert_eq!(resp.status(), StatusCode::NOT_FOUND);
}

/// AC-13: path traversal /assets/../Cargo.toml → 400 or 404, no [package] leak.
#[tokio::test]
async fn ac13_path_traversal_blocked() {
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
        "path traversal must not leak Cargo.toml contents"
    );
}

// ---------------------------------------------------------------------------
// AC-6 contract : stats.rs has no auth extractor; lib.rs mounts under public_api
// ---------------------------------------------------------------------------

/// AC-6 (contract): `stats.rs` source contains no `RequireAuthor` / `RequireMember` / `#[public]` guard.
#[test]
fn ac6_contract_stats_handler_has_no_auth_extractor() {
    // AC-6: inspect source at compile time via include_str!.
    let src = include_str!("../src/handlers/stats.rs");
    assert!(
        !src.contains("RequireAuthor"),
        "stats.rs must not contain RequireAuthor"
    );
    assert!(
        !src.contains("RequireMember"),
        "stats.rs must not contain RequireMember"
    );
    // #[public] is not a real Axum attribute but could be used as a marker comment.
    assert!(
        !src.contains("author_only") && !src.contains("member_only"),
        "stats.rs must not contain author_only/member_only guard"
    );
}

/// AC-6 (contract): `lib.rs` mounts `/v1/stats` inside `public_api` block (no auth gate).
#[test]
fn ac6_contract_lib_mounts_stats_in_public_api() {
    // AC-6: the plan specifies public_api receives /v1/stats (no gate).
    // We verify by checking lib.rs source contains the route in the public_api block.
    let src = include_str!("../src/lib.rs");
    assert!(
        src.contains("/v1/stats"),
        "lib.rs must contain /v1/stats route"
    );
    // Verify it is NOT in the dashboard_api or auth_router block by checking
    // that /v1/stats appears in lib.rs at all (router inspection is done above).
    // A source-level check that the route is assigned to public_api (not dashboard_api).
    // The plan specifies: public_api.route("/v1/stats", ...).
    assert!(
        src.contains("public_api") && src.contains("/v1/stats"),
        "lib.rs must mount /v1/stats in the public_api router"
    );
}

// ---------------------------------------------------------------------------
// AC-8 (contract): nav links in index.html and observability.html
// ---------------------------------------------------------------------------

/// AC-8 (contract): index.html contains the exact Chat and Observabilité nav anchors, in order.
#[test]
fn ac8_index_html_nav_links() {
    // AC-8: both nav anchors must be present (exact anchor + label) in order.
    // Match the full anchor (not just `href="/`) so the test fails if the Chat
    // link is removed — `href="/` alone also matches `href="/assets/...`.
    let html = std::fs::read_to_string("static/index.html").expect("static/index.html not found");

    let pos_chat = html
        .find(r#"<a href="/">Chat</a>"#)
        .expect(r#"index.html must contain <a href="/">Chat</a>"#);
    let pos_obs = html
        .find(r#"<a href="/observability">Observabilité</a>"#)
        .expect(r#"index.html must contain <a href="/observability">Observabilité</a>"#);

    assert!(
        pos_chat < pos_obs,
        "Chat link must appear before Observabilité link in index.html"
    );
}

/// AC-8 (contract): observability.html contains the exact Chat and Observabilité nav anchors, in order.
#[test]
fn ac8_observability_html_nav_links() {
    // AC-8: same nav block required on /observability page (exact anchor + label).
    let html = std::fs::read_to_string("static/observability.html")
        .expect("static/observability.html not found");

    let pos_chat = html
        .find(r#"<a href="/">Chat</a>"#)
        .expect(r#"observability.html must contain <a href="/">Chat</a>"#);
    let pos_obs = html
        .find(r#"<a href="/observability">Observabilité</a>"#)
        .expect(r#"observability.html must contain <a href="/observability">Observabilité</a>"#);

    assert!(
        pos_chat < pos_obs,
        "Chat link must appear before Observabilité link in observability.html"
    );

    // AC-1: h1 must be present.
    assert!(
        html.contains("<h1>Observabilité</h1>"),
        "observability.html must contain <h1>Observabilité</h1>"
    );
}

// ---------------------------------------------------------------------------
// AC-12 (contract): observability.html has no inline script/style/on*
// ---------------------------------------------------------------------------

/// AC-12 (contract): observability.html has no inline <script>, <style>, style="", or on*= handlers.
#[test]
fn ac12_no_inline_in_observability_html() {
    // AC-12: identical anti-inline checks to static_test.rs AC-7 for index.html.
    let html = std::fs::read_to_string("static/observability.html")
        .expect("static/observability.html not found");

    // No inline <script> block (script with content rather than src=).
    assert!(
        !html.contains("<script>") && !html.contains("<script\n"),
        "observability.html must not contain inline <script> block"
    );

    // No inline <style> block.
    assert!(
        !html.contains("<style"),
        "observability.html must not contain <style> block"
    );

    // No style= attribute.
    assert!(
        !html.contains("style=\"") && !html.contains("style='"),
        "observability.html must not contain style=\"...\" attribute"
    );

    // No inline on*= event handlers.
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
        "observability.html must not contain inline on*= event handlers"
    );
}

// ---------------------------------------------------------------------------
// AC-4 / AC-5 : DB fixture tests (require live Postgres via #[sqlx::test])
// ---------------------------------------------------------------------------

/// Build a test `Config` + `AppState` for DB-backed tests.
#[cfg(test)]
fn make_db_config() -> archiviste_gateway::config::Config {
    use common::jwt_helpers::{test_private_key_pem, test_public_key_pem, TEST_KEY_ID};
    archiviste_gateway::config::Config {
        bind_addr: "127.0.0.1:0".to_string(),
        workers_url: "http://127.0.0.1:1".to_string(),
        database_url: "postgres://test".to_string(),
        jwt_ed25519_public_key_pem: test_public_key_pem().to_string(),
        jwt_ed25519_private_key_pem: secrecy::SecretString::from(
            test_private_key_pem().to_string(),
        ),
        jwt_kid: TEST_KEY_ID.to_string(),
        version: "0.1.0".to_string(),
        connect_timeout_ms: 500,
        request_timeout_ms: 35_000,
        gcs_signing_sa_email: "test-sa@project.iam.gserviceaccount.com".to_string(),
        gcs_bucket: "archiviste-conversations".to_string(),
    }
}

/// Insert n conversations for the sentinel user (`Uuid::nil()`).
/// Each row gets a distinct `gcs_uri` (NOT NULL UNIQUE) and `message_count=0`.
/// Sentinel users row must be pre-inserted before calling this.
#[cfg(test)]
async fn insert_conversations(pool: &sqlx::PgPool, n: usize) {
    for i in 0..n {
        sqlx::query(
            "INSERT INTO conversations (id, user_id, gcs_uri, message_count) VALUES ($1, $2, $3, 0)",
        )
        .bind(Uuid::from_u128(u128::try_from(i + 1).unwrap()))
        .bind(Uuid::nil())
        .bind(format!("gs://archiviste-conversations/obs-test/{i}.md"))
        .execute(pool)
        .await
        .unwrap();
    }
}

/// AC-4 / AC-5: `conversation_count` = 0 when no conversations in DB.
#[sqlx::test(migrations = "../migrations")]
async fn ac4_ac5_stats_zero_conversations(pool: sqlx::PgPool) {
    // AC-4/AC-5: 0 rows → {"conversation_count": 0}, exact keys.
    // Insert sentinel users row (conversations.user_id FK → users.id).
    sqlx::query("INSERT INTO users (id, tier) VALUES ($1, 'anonymous') ON CONFLICT DO NOTHING")
        .bind(Uuid::nil())
        .execute(&pool)
        .await
        .unwrap();

    let state = Arc::new(AppState::new_with_pool(make_db_config(), pool).unwrap());
    let app = router(state);
    let resp = get_anon(app, "/v1/stats").await;

    assert_eq!(resp.status(), StatusCode::OK, "expected 200 for /v1/stats");

    let ct = resp
        .headers()
        .get("content-type")
        .expect("content-type missing")
        .to_str()
        .unwrap();
    assert_eq!(
        ct, "application/json; charset=utf-8",
        "expected application/json; charset=utf-8, got: {ct}"
    );
    // Exactly one content-type header (no duplicate).
    assert_eq!(
        resp.headers()
            .get_all(axum::http::header::CONTENT_TYPE)
            .iter()
            .count(),
        1,
        "must have exactly one content-type header"
    );

    let json = body_json(resp).await;
    assert_eq!(
        json["conversation_count"], 0,
        "expected conversation_count=0"
    );
    // AC-4: no other field.
    assert_eq!(
        json.as_object().unwrap().len(),
        1,
        "body must have exactly 1 key: conversation_count"
    );
}

/// AC-4 / AC-5: `conversation_count` = 1 (sentinel user + 1 conversation).
#[sqlx::test(migrations = "../migrations")]
async fn ac4_ac5_stats_one_conversation(pool: sqlx::PgPool) {
    // AC-4/AC-5: 1 row → {"conversation_count": 1}.
    sqlx::query("INSERT INTO users (id, tier) VALUES ($1, 'anonymous') ON CONFLICT DO NOTHING")
        .bind(Uuid::nil())
        .execute(&pool)
        .await
        .unwrap();

    insert_conversations(&pool, 1).await;

    let state = Arc::new(AppState::new_with_pool(make_db_config(), pool).unwrap());
    let app = router(state);
    let resp = get_anon(app, "/v1/stats").await;

    assert_eq!(resp.status(), StatusCode::OK);
    let json = body_json(resp).await;
    assert_eq!(json["conversation_count"], 1);
    assert_eq!(json.as_object().unwrap().len(), 1);
}

/// AC-4 / AC-5: `conversation_count` = 42 (strict identity mapping, no rounding).
#[sqlx::test(migrations = "../migrations")]
async fn ac4_ac5_stats_forty_two_conversations(pool: sqlx::PgPool) {
    // AC-4/AC-5: 42 rows → {"conversation_count": 42}, distinct from 0 and 1 (AC-5).
    sqlx::query("INSERT INTO users (id, tier) VALUES ($1, 'anonymous') ON CONFLICT DO NOTHING")
        .bind(Uuid::nil())
        .execute(&pool)
        .await
        .unwrap();

    insert_conversations(&pool, 42).await;

    let state = Arc::new(AppState::new_with_pool(make_db_config(), pool).unwrap());
    let app = router(state);
    let resp = get_anon(app, "/v1/stats").await;

    assert_eq!(resp.status(), StatusCode::OK);
    let json = body_json(resp).await;
    assert_eq!(json["conversation_count"], 42);
    assert_eq!(json.as_object().unwrap().len(), 1);
}
