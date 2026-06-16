//! Tests for IDN-001 — cookie-dominant anonymous identity.
//!
//! AC: same cookie ⇒ same user_id regardless of IP/User-Agent.
//!     different cookie ⇒ different user_id.
//!     missing/invalid cookie ⇒ fresh cookie issued, no crash.

#![allow(clippy::unwrap_used)]
// Test doc comments reference identifiers (user_id, UUIDv5, …) as prose; the
// pedantic doc_markdown lint is suppressed for tests (cf. test_observability_status.rs).
#![allow(clippy::doc_markdown)]

mod common;
use common::jwt_helpers::make_test_config;

use archiviste_gateway::{
    auth::fingerprint::{cookie_uuid_to_user_id, parse_anon_cookie, ANON_COOKIE_NAME},
    router,
    state::AppState,
};
use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use http_body_util::BodyExt;
use proptest::prelude::*;
use std::sync::Arc;
use tower::ServiceExt;
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

fn make_state() -> Arc<AppState> {
    let mut config = make_test_config("http://127.0.0.1:1");
    config.request_timeout_ms = 5_000;
    Arc::new(AppState::new(config).unwrap())
}

fn cookie_header(uuid: Uuid) -> String {
    format!("{ANON_COOKIE_NAME}={uuid}")
}

// ---------------------------------------------------------------------------
// Unit tests: pure function `cookie_uuid_to_user_id`
// ---------------------------------------------------------------------------

/// IDN-001 AC: same cookie UUID ⇒ same user_id (deterministic).
#[test]
fn same_cookie_produces_same_user_id() {
    // IDN-001: same cookie ⇒ same user_id regardless of any other context.
    let cookie = Uuid::new_v4();
    let id_a = cookie_uuid_to_user_id(&cookie);
    let id_b = cookie_uuid_to_user_id(&cookie);
    assert_eq!(id_a, id_b);
}

/// IDN-001 AC: different cookie ⇒ different user_id.
#[test]
fn different_cookie_produces_different_user_id() {
    // IDN-001: different cookie ⇒ different user_id (collision-resistant UUIDv5).
    let id_a = cookie_uuid_to_user_id(&Uuid::new_v4());
    let id_b = cookie_uuid_to_user_id(&Uuid::new_v4());
    assert_ne!(id_a, id_b);
}

/// IDN-001 AC: user_id is independent of IP / User-Agent.
/// The pure function takes no IP/UA — this test verifies the signature directly.
#[test]
fn user_id_depends_only_on_cookie() {
    // IDN-001: cookie_uuid_to_user_id has no IP/UA parameter — independence
    // is enforced structurally at the type level.
    let cookie = Uuid::new_v4();
    let id = cookie_uuid_to_user_id(&cookie);
    // Calling again with identical cookie, different call-site context ⇒ same result.
    assert_eq!(id, cookie_uuid_to_user_id(&cookie));
}

/// IDN-001: derived user_id is distinct from the cookie UUID itself.
#[test]
fn user_id_differs_from_cookie_uuid() {
    // IDN-001: UUIDv5 over cookie bytes produces a structurally distinct UUID.
    let cookie = Uuid::new_v4();
    let user_id = cookie_uuid_to_user_id(&cookie);
    assert_ne!(
        user_id, cookie,
        "user_id must not equal the raw cookie UUID"
    );
}

// ---------------------------------------------------------------------------
// Unit test: `parse_anon_cookie`
// ---------------------------------------------------------------------------

/// IDN-001: valid cookie is parsed as a `Uuid`.
#[test]
fn parse_anon_cookie_returns_uuid_for_valid_cookie() {
    let uuid = Uuid::new_v4();
    let mut headers = axum::http::HeaderMap::new();
    headers.insert(
        axum::http::header::COOKIE,
        format!("{ANON_COOKIE_NAME}={uuid}").parse().unwrap(),
    );
    assert_eq!(parse_anon_cookie(&headers), Some(uuid));
}

/// IDN-001: invalid (non-UUID) cookie value ⇒ None (caller issues a fresh cookie).
#[test]
fn parse_anon_cookie_returns_none_for_non_uuid_value() {
    // IDN-001 AC: invalid cookie ⇒ None (not panic).
    let mut headers = axum::http::HeaderMap::new();
    headers.insert(
        axum::http::header::COOKIE,
        format!("{ANON_COOKIE_NAME}=not-a-uuid").parse().unwrap(),
    );
    assert!(parse_anon_cookie(&headers).is_none());
}

/// IDN-001: absent cookie ⇒ None (caller issues a fresh cookie).
#[test]
fn parse_anon_cookie_returns_none_when_absent() {
    // IDN-001 AC: missing cookie ⇒ None (no crash).
    let headers = axum::http::HeaderMap::new();
    assert!(parse_anon_cookie(&headers).is_none());
}

// ---------------------------------------------------------------------------
// Integration tests: GET /v1/me end-to-end
// ---------------------------------------------------------------------------

/// IDN-001 AC: same cookie ⇒ same user_id on GET /v1/me.
#[tokio::test]
async fn get_me_same_cookie_same_user_id() {
    // IDN-001 AC: same cookie ⇒ same user_id regardless of other request context.
    let cookie = Uuid::new_v4();
    let cookie_hdr = cookie_header(cookie);

    let resp_a = router(make_state())
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/v1/me")
                .header("cookie", &cookie_hdr)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    let resp_b = router(make_state())
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/v1/me")
                .header("cookie", &cookie_hdr)
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(resp_a.status(), StatusCode::OK);
    assert_eq!(resp_b.status(), StatusCode::OK);

    let body_a: serde_json::Value =
        serde_json::from_slice(&resp_a.into_body().collect().await.unwrap().to_bytes()).unwrap();
    let body_b: serde_json::Value =
        serde_json::from_slice(&resp_b.into_body().collect().await.unwrap().to_bytes()).unwrap();

    assert_eq!(body_a["user_id"], body_b["user_id"]);
    assert_eq!(body_a["tier"], "anonymous");
}

/// IDN-001 AC: different cookie ⇒ different user_id on GET /v1/me.
#[tokio::test]
async fn get_me_different_cookie_different_user_id() {
    // IDN-001 AC: different cookie ⇒ different user_id.
    let state = make_state();

    let fetch_user_id = |cookie: Uuid| {
        let state = Arc::clone(&state);
        async move {
            let resp = router(state)
                .oneshot(
                    Request::builder()
                        .method("GET")
                        .uri("/v1/me")
                        .header("cookie", cookie_header(cookie))
                        .body(Body::empty())
                        .unwrap(),
                )
                .await
                .unwrap();
            let body: serde_json::Value =
                serde_json::from_slice(&resp.into_body().collect().await.unwrap().to_bytes())
                    .unwrap();
            body["user_id"].as_str().unwrap().to_string()
        }
    };

    let id_a = fetch_user_id(Uuid::new_v4()).await;
    let id_b = fetch_user_id(Uuid::new_v4()).await;
    assert_ne!(id_a, id_b);
}

/// IDN-001 AC: user_id is independent of User-Agent when cookie is held constant.
#[tokio::test]
async fn get_me_user_id_independent_of_user_agent() {
    // IDN-001 AC: vary User-Agent, hold cookie — user_id must not change.
    let cookie = Uuid::new_v4();
    let cookie_hdr = cookie_header(cookie);

    let fetch = |ua: &'static str| {
        let cookie_hdr = cookie_hdr.clone();
        async move {
            let resp = router(make_state())
                .oneshot(
                    Request::builder()
                        .method("GET")
                        .uri("/v1/me")
                        .header("cookie", &cookie_hdr)
                        .header("user-agent", ua)
                        .body(Body::empty())
                        .unwrap(),
                )
                .await
                .unwrap();
            let body: serde_json::Value =
                serde_json::from_slice(&resp.into_body().collect().await.unwrap().to_bytes())
                    .unwrap();
            body["user_id"].as_str().unwrap().to_string()
        }
    };

    let id_firefox =
        fetch("Mozilla/5.0 (Windows NT 10.0; rv:109.0) Gecko/20100101 Firefox/115.0").await;
    let id_curl = fetch("curl/8.1.2").await;
    let id_empty = fetch("").await;

    assert_eq!(
        id_firefox, id_curl,
        "user_id must not change when User-Agent changes"
    );
    assert_eq!(
        id_firefox, id_empty,
        "user_id must not change when User-Agent is absent"
    );
}

/// IDN-001 AC: missing cookie ⇒ Set-Cookie issued, no crash, 200 returned.
#[tokio::test]
async fn get_me_missing_cookie_issues_set_cookie_and_returns_200() {
    // IDN-001 AC: missing cookie ⇒ fresh cookie issued, defined behavior, no crash.
    let resp = router(make_state())
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/v1/me")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(resp.status(), StatusCode::OK);

    let set_cookie = resp
        .headers()
        .get("set-cookie")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");

    assert!(
        set_cookie.contains(ANON_COOKIE_NAME),
        "Set-Cookie must carry {ANON_COOKIE_NAME}; got: {set_cookie}"
    );
    assert!(set_cookie.contains("HttpOnly"), "cookie must be HttpOnly");
    assert!(set_cookie.contains("Secure"), "cookie must be Secure");
    assert!(
        set_cookie.contains("SameSite=Lax"),
        "cookie must be SameSite=Lax"
    );
}

/// IDN-001 AC: invalid (non-UUID) cookie ⇒ new cookie issued, no crash.
#[tokio::test]
async fn get_me_invalid_cookie_issues_new_cookie() {
    // IDN-001 AC: invalid cookie ⇒ fresh cookie issued (not panic).
    let resp = router(make_state())
        .oneshot(
            Request::builder()
                .method("GET")
                .uri("/v1/me")
                .header("cookie", format!("{ANON_COOKIE_NAME}=not-a-uuid"))
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(resp.status(), StatusCode::OK);

    let set_cookie = resp
        .headers()
        .get("set-cookie")
        .and_then(|v| v.to_str().ok())
        .unwrap_or("");

    assert!(
        set_cookie.contains(ANON_COOKIE_NAME),
        "invalid cookie must trigger a fresh Set-Cookie; got: {set_cookie}"
    );
}

// ---------------------------------------------------------------------------
// Property test: same cookie ⇒ same user_id; different cookie ⇒ different id
// ---------------------------------------------------------------------------

proptest! {
    /// INV-IDN-001a: same cookie UUID always yields the same user_id.
    #[test]
    fn prop_same_cookie_same_user_id(
        cookie_bytes in proptest::array::uniform16(0u8..=255u8)
    ) {
        // IDN-001: deterministic — same cookie ⇒ same user_id.
        let cookie = Uuid::from_bytes(cookie_bytes);
        let id_a = cookie_uuid_to_user_id(&cookie);
        let id_b = cookie_uuid_to_user_id(&cookie);
        prop_assert_eq!(id_a, id_b);
    }

    /// INV-IDN-001b: distinct cookie UUIDs yield distinct user_ids.
    #[test]
    fn prop_distinct_cookies_distinct_user_ids(
        bytes_a in proptest::array::uniform16(0u8..=255u8),
        bytes_b in proptest::array::uniform16(0u8..=255u8),
    ) {
        // IDN-001: different cookie ⇒ different user_id (collision avoidance).
        prop_assume!(bytes_a != bytes_b);
        let id_a = cookie_uuid_to_user_id(&Uuid::from_bytes(bytes_a));
        let id_b = cookie_uuid_to_user_id(&Uuid::from_bytes(bytes_b));
        prop_assert_ne!(id_a, id_b);
    }
}
