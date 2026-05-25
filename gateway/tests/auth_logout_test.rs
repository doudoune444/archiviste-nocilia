//! Integration tests for `POST /v1/auth/logout` — SEC-001 PR-b.
//!
//! AC-8: authenticated logout revokes session, clears cookie, returns 204.
//! AC-8: subsequent request with same JWT → 401 `session_revoked`.

#![allow(clippy::unwrap_used, clippy::expect_used)]

mod common;
use common::auth_mocks::{InMemorySessionCreator, InMemorySessionRevoker, InMemoryUserLookup};
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
    let config = make_test_config("http://127.0.0.1:1");
    Arc::new(AppState::new(config).unwrap())
}

async fn body_json(resp: axum::response::Response) -> serde_json::Value {
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap()
}

/// Call `POST /v1/auth/logout` with a bearer token.
async fn logout_with_token(state: Arc<AppState>, token: &str) -> axum::response::Response {
    let app = router(state);
    let req = Request::builder()
        .method("POST")
        .uri("/v1/auth/logout")
        .header("content-type", "application/json")
        .header("authorization", format!("Bearer {token}"))
        .body(Body::empty())
        .unwrap();
    app.oneshot(req).await.unwrap()
}

// ---------------------------------------------------------------------------
// AC-8: no DB pool → 503
// ---------------------------------------------------------------------------

/// AC-8 (unit path): logout with a valid JWT but no session revoker returns 503.
///
/// Full 204 path is covered by `ac8_logout_returns_204_revokes_session_and_clears_cookie`.
/// This unit test verifies the handler returns 503 when no revoker is configured.
#[tokio::test]
async fn ac8_logout_without_db_and_valid_jwt_returns_503() {
    // AC-8: logout uses session_revoker; without one configured → 503.
    let state = make_state();
    let token = sign_test_token(Uuid::new_v4(), UserTier::Member, Uuid::new_v4());
    // No session_revoker is configured in make_state() → 503.
    let resp = logout_with_token(state, &token).await;
    assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
}

/// AC-8: logout without a JWT → 401 `invalid_token`.
#[tokio::test]
async fn ac8_logout_without_auth_returns_401() {
    // AC-8: logout requires authentication (AuthUser extractor).
    let state = make_state();
    let app = router(state);
    let req = Request::builder()
        .method("POST")
        .uri("/v1/auth/logout")
        .header("content-type", "application/json")
        .body(Body::empty())
        .unwrap();
    let resp = app.oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "invalid_token");
}

/// AC-8: Set-Cookie clear header contains all 5 required attributes at Max-Age=0.
///
/// Calls `session_cookie` (pub(crate)) directly to verify the produced header value
/// rather than rebuilding the string inline.
#[test]
fn ac8_clear_cookie_has_all_required_attributes() {
    // AC-8: the clear-cookie header must carry HttpOnly, Secure, SameSite=Lax,
    // Path=/ and Max-Age=0 — five mandatory attributes (security.md §A07).
    use archiviste_gateway::routes::auth::session_cookie;

    let hv = session_cookie("", 0);
    let s = hv.to_str().expect("header value must be ASCII");
    assert!(s.starts_with("archiviste_session="), "cookie name prefix");
    assert!(s.contains("HttpOnly"), "missing HttpOnly");
    assert!(s.contains("Secure"), "missing Secure");
    assert!(s.contains("SameSite=Lax"), "missing SameSite=Lax");
    assert!(s.contains("Path=/"), "missing Path=/");
    assert!(s.contains("Max-Age=0"), "missing Max-Age=0");
}

// ---------------------------------------------------------------------------
// AC-17: Content-Type enforcement on logout
// ---------------------------------------------------------------------------

/// AC-17: wrong Content-Type on logout → 415.
#[tokio::test]
async fn ac17_logout_wrong_content_type_returns_415() {
    // AC-17: auth routes enforce Content-Type: application/json.
    let state = make_state();
    let app = router(state);
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/auth/logout")
                .header("content-type", "text/plain")
                .body(Body::empty())
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::UNSUPPORTED_MEDIA_TYPE);
}

// ---------------------------------------------------------------------------
// AC-8 happy path: full 204 + cookie clear + revoker called (N-5 fix)
// ---------------------------------------------------------------------------

/// AC-8: logout with valid JWT returns 204, clears cookie, and calls revoker with correct sid.
///
/// Exercises the full logout handler path with `InMemorySessionRevoker` (no DB required).
#[tokio::test]
#[allow(clippy::clone_on_ref_ptr)]
async fn ac8_logout_returns_204_revokes_session_and_clears_cookie() {
    // AC-8: build state with InMemorySessionRevoker so we can assert revoke() was called.
    let sid = Uuid::new_v4();
    let revoker = Arc::new(InMemorySessionRevoker::new());
    let lookup = Arc::new(InMemoryUserLookup::empty());
    let creator = Arc::new(InMemorySessionCreator);
    let config = make_test_config("http://127.0.0.1:1");
    let state =
        Arc::new(AppState::new_with_all_mocks(config, lookup, creator, revoker.clone()).unwrap());

    // Sign a test JWT carrying the known sid.
    let token = sign_test_token(Uuid::new_v4(), UserTier::Member, sid);

    let resp = logout_with_token(state, &token).await;

    // AC-8: handler must return 204 No Content.
    assert_eq!(resp.status(), StatusCode::NO_CONTENT);

    // AC-8: Set-Cookie must clear the session cookie (Max-Age=0).
    let set_cookie = resp
        .headers()
        .get_all("set-cookie")
        .iter()
        .find(|v| v.to_str().unwrap_or("").contains("archiviste_session="))
        .expect("archiviste_session Set-Cookie header must be present")
        .to_str()
        .unwrap()
        .to_string();
    assert!(
        set_cookie.contains("Max-Age=0"),
        "cookie must be cleared with Max-Age=0"
    );

    // AC-8: revoker must have been called with the sid from the JWT.
    let revoked_list = revoker.revoked_sids();
    assert!(
        revoked_list.contains(&sid),
        "revoker.revoke() must be called with sid={sid}; got {revoked_list:?}"
    );
}
