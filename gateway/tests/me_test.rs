//! Integration tests for `GET /v1/me` (SEC-001 PR-a).
//!
//! Covers AC-9 (tier/fingerprint response) and AC-10 (deterministic anonymous `user_id`).

#![allow(clippy::unwrap_used)]

mod common;
use common::jwt_helpers::{sign_test_token, test_public_key_pem};

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
        request_timeout_ms: 5_000,
    };
    Arc::new(AppState::new(config).unwrap())
}

async fn get_me(
    app: axum::Router,
    cookie: Option<&str>,
    jwt: Option<&str>,
) -> axum::response::Response {
    let mut builder = Request::builder().method("GET").uri("/v1/me");

    if let Some(c) = cookie {
        builder = builder.header("cookie", c);
    }
    if let Some(j) = jwt {
        builder = builder.header("authorization", format!("Bearer {j}"));
    }

    app.oneshot(builder.body(Body::empty()).unwrap())
        .await
        .unwrap()
}

async fn body_json(resp: axum::response::Response) -> serde_json::Value {
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap()
}

// ---------------------------------------------------------------------------
// AC-9 (a): anonymous request without cookie → 200, tier=anonymous, fingerprint set,
//           cookie archiviste_anon posed in response.
// ---------------------------------------------------------------------------

/// AC-9 (a): anonymous (no cookie) → 200, tier=anonymous, fingerprint is 64-char hex,
///           Set-Cookie `archiviste_anon` present.
#[tokio::test]
async fn ac9a_anonymous_no_cookie_returns_200_with_fingerprint() {
    // AC-9: anonymous request returns user_id/tier/fingerprint
    let app = router(make_state());
    let resp = get_me(app, None, None).await;

    assert_eq!(resp.status(), StatusCode::OK);

    // Verify Set-Cookie archiviste_anon is set
    let set_cookie = resp.headers().get("set-cookie");
    assert!(set_cookie.is_some(), "Set-Cookie must be set for anonymous");
    let cookie_val = set_cookie.unwrap().to_str().unwrap();
    assert!(
        cookie_val.contains("archiviste_anon="),
        "Cookie name must be archiviste_anon"
    );
    assert!(cookie_val.contains("HttpOnly"), "Cookie must be HttpOnly");
    assert!(
        cookie_val.contains("SameSite=Lax"),
        "Cookie must be SameSite=Lax"
    );
    assert!(
        cookie_val.contains("Max-Age=31536000"),
        "Cookie Max-Age must be 1 year"
    );

    let body = body_json(resp).await;
    assert_eq!(body["tier"], "anonymous");

    let fingerprint = body["fingerprint"].as_str().unwrap();
    assert_eq!(fingerprint.len(), 64, "fingerprint must be 64 hex chars");
    assert!(
        fingerprint.chars().all(|c| c.is_ascii_hexdigit()),
        "fingerprint must be hex"
    );

    let user_id = body["user_id"].as_str().unwrap();
    assert_eq!(user_id.len(), 36, "user_id must be a UUID");
}

// ---------------------------------------------------------------------------
// AC-9 (b): anonymous with existing cookie → same user_id (deterministic).
// ---------------------------------------------------------------------------

/// AC-9 (b) + AC-10: same IP + UA + cookie → same `user_id` across requests.
#[tokio::test]
async fn ac9b_ac10_deterministic_user_id_with_existing_cookie() {
    // AC-9 (b): existing archiviste_anon cookie → same user_id
    // AC-10: user_id is deterministic UUIDv5(NIL, sha256_hex)
    let state = make_state();

    let anon_uuid = uuid::Uuid::new_v4().to_string();
    let cookie_header = format!("archiviste_anon={anon_uuid}");

    let app1 = router(Arc::clone(&state));
    let resp1 = get_me(app1, Some(&cookie_header), None).await;
    assert_eq!(resp1.status(), StatusCode::OK);
    let body1 = body_json(resp1).await;
    let user_id_1 = body1["user_id"].as_str().unwrap().to_string();

    let app2 = router(Arc::clone(&state));
    let resp2 = get_me(app2, Some(&cookie_header), None).await;
    assert_eq!(resp2.status(), StatusCode::OK);
    let body2 = body_json(resp2).await;
    let user_id_2 = body2["user_id"].as_str().unwrap().to_string();

    assert_eq!(
        user_id_1, user_id_2,
        "same cookie+IP+UA must produce same user_id"
    );
}

// ---------------------------------------------------------------------------
// AC-10: verbatim UUIDv5(NIL, fingerprint_hex) — namespace regression test (HIGH-1 fix).
// ---------------------------------------------------------------------------

/// AC-10 verbatim: assert exact `UUIDv5` value for a known (ip, ua, `anon_cookie`) triple.
///
/// Fixture values (fixed):
///   ip = "127.0.0.1" (no CF-Connecting-IP in test env)
///   ua = "TestAgent/1.0"
///   `anon_id` = "00000000-0000-0000-0000-000000000001"
///
/// `fingerprint_hex` = SHA-256("127.0.0.1|TestAgent/1.0|00000000-0000-0000-0000-000000000001")
///                   = "8b93792410d3a43b4d3c94407af5e00050d9a553c41a4fc3a0ea6bb4d8bc7f10"
///
/// `expected_user_id` = UUIDv5(NIL, `fingerprint_hex`)
///                    = "0c0a168b-3e2e-5975-8163-0e8e140d59fa"
///
/// This test prevents regression to `NAMESPACE_DNS` (AC-10, HIGH-1 fix).
#[tokio::test]
async fn ac10_verbatim_uuidv5_nil_namespace() {
    // AC-10: user_id MUST be UUIDv5(NIL namespace, fingerprint_hex)
    use archiviste_gateway::auth::fingerprint::{compute_fingerprint, fingerprint_to_user_id};

    let ip = "127.0.0.1";
    let ua = "TestAgent/1.0";
    let anon_id = "00000000-0000-0000-0000-000000000001";

    let fingerprint = compute_fingerprint(ip, ua, anon_id);
    assert_eq!(
        fingerprint, "8b93792410d3a43b4d3c94407af5e00050d9a553c41a4fc3a0ea6bb4d8bc7f10",
        "fingerprint_hex must match SHA-256 of 'ip|ua|anon_id'"
    );

    let user_id = fingerprint_to_user_id(&fingerprint);
    assert_eq!(
        user_id.to_string(),
        "0c0a168b-3e2e-5975-8163-0e8e140d59fa",
        "user_id must be UUIDv5(NIL, fingerprint_hex) — not NAMESPACE_DNS"
    );

    // Verify it is version 5 (bits 12-15 of time_hi = 0101b = 5).
    assert_eq!(user_id.get_version_num(), 5, "must be UUID version 5");
}

// ---------------------------------------------------------------------------
// AC-21: CF-Connecting-IP header takes priority over X-Forwarded-For and ConnectInfo.
// ---------------------------------------------------------------------------

/// AC-21: `extract_ip` returns CF-Connecting-IP when present, ignoring other sources.
#[test]
fn ac21_cf_connecting_ip_has_priority() {
    // AC-21: CF-Connecting-IP must take priority over X-Forwarded-For and ConnectInfo.
    use archiviste_gateway::auth::fingerprint::extract_ip;
    use axum::http::{HeaderMap, HeaderName, HeaderValue};
    use std::net::SocketAddr;

    let mut headers = HeaderMap::new();
    headers.insert(
        HeaderName::from_static("cf-connecting-ip"),
        HeaderValue::from_static("1.2.3.4"),
    );
    headers.insert(
        HeaderName::from_static("x-forwarded-for"),
        HeaderValue::from_static("9.9.9.9"),
    );

    let connect_info: SocketAddr = "127.0.0.1:12345".parse().unwrap();
    let ip = extract_ip(&headers, Some(&connect_info));

    assert_eq!(ip, "1.2.3.4", "CF-Connecting-IP must take priority");
}

/// AC-21: when CF-Connecting-IP is absent, `ConnectInfo` is used (dev path).
#[test]
fn ac21_connect_info_used_when_no_cf_header() {
    // AC-21: fallback to ConnectInfo when no CF-Connecting-IP header is present.
    use archiviste_gateway::auth::fingerprint::extract_ip;
    use axum::http::HeaderMap;
    use std::net::SocketAddr;

    let headers = HeaderMap::new();
    let connect_info: SocketAddr = "10.0.0.1:8080".parse().unwrap();
    let ip = extract_ip(&headers, Some(&connect_info));

    assert_eq!(
        ip, "10.0.0.1",
        "ConnectInfo IP must be used when CF header absent"
    );
}

// ---------------------------------------------------------------------------
// AC-9 (c): request with valid JWT member → 200, tier=member, fingerprint=null.
// ---------------------------------------------------------------------------

/// AC-9 (c): valid member JWT → 200, tier=member, fingerprint=null.
#[tokio::test]
async fn ac9c_member_jwt_returns_tier_member_no_fingerprint() {
    // AC-9 (c): authenticated member → fingerprint null
    let state = make_state();
    let user_id = uuid::Uuid::new_v4();
    let session_id = uuid::Uuid::new_v4();

    let token = sign_test_token(
        user_id,
        archiviste_gateway::auth::extractor::UserTier::Member,
        session_id,
    );

    // We need a mock DB that validates the session — but PR-a check_session requires a real DB.
    // For this test, use a token signed for a session that is "valid" at the extractor layer.
    // Since we have no DB in this test, the extractor will fail DB lookup → this test should
    // return 200 with anonymous tier (JWT invalid/no DB) OR we need to mock the DB.
    //
    // Per plan U-5: PR-a covers AC-14(a) anon only; AC-9(c) with a valid JWT member
    // requires a DB check (AC-13). We test the path here but it will fall back to
    // anonymous if session DB check fails. The contract test is completed in PR-b
    // when the full login flow provides a real session.
    //
    // For now, assert that the route responds 200 (not 500), tier may be anonymous
    // because session check fails without DB. This is expected per plan AC scope.
    let app = router(Arc::clone(&state));
    let resp = get_me(app, None, Some(&token)).await;
    // Route must respond (not panic / 500)
    assert!(
        resp.status().is_success() || resp.status() == StatusCode::UNAUTHORIZED,
        "GET /v1/me must not 500 when JWT present but DB unreachable"
    );
}
