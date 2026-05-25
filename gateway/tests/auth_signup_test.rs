//! Integration tests for `POST /v1/auth/signup` -- SEC-001 PR-b.
//!
//! AC-1: 201 + body `{user_id, tier:"member"}` + no `Set-Cookie`.
//! AC-2: 409 `email_taken` when email already exists.
//! AC-3: 400 `invalid_request` on malformed email/password.
//! AC-17: 415 on wrong Content-Type; 413 on body > 4 KiB.

#![allow(clippy::unwrap_used)]

mod common;
use common::auth_mocks::{InMemorySessionCreator, InMemoryUserLookup};
use common::jwt_helpers::make_test_config;

use archiviste_gateway::{router, state::AppState};
use axum::{
    body::Body,
    http::{Request, StatusCode},
};
use http_body_util::BodyExt;
use std::sync::Arc;
use tower::ServiceExt;

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn make_state_no_db() -> Arc<AppState> {
    let config = make_test_config("http://127.0.0.1:1");
    Arc::new(AppState::new(config).unwrap())
}

async fn body_json(resp: axum::response::Response) -> serde_json::Value {
    let bytes = resp.into_body().collect().await.unwrap().to_bytes();
    serde_json::from_slice(&bytes).unwrap()
}

async fn signup(state: Arc<AppState>, body: &str) -> axum::response::Response {
    let app = router(state);
    let req = Request::builder()
        .method("POST")
        .uri("/v1/auth/signup")
        .header("content-type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap();
    app.oneshot(req).await.unwrap()
}

fn make_state_with_empty_lookup() -> Arc<AppState> {
    let lookup = Arc::new(InMemoryUserLookup::empty());
    let creator = Arc::new(InMemorySessionCreator);
    let config = make_test_config("http://127.0.0.1:1");
    Arc::new(AppState::new_with_mocks(config, lookup, creator).unwrap())
}

// ---------------------------------------------------------------------------
// AC-1: 201 + {user_id, tier:"member"} + no Set-Cookie
// ---------------------------------------------------------------------------

/// AC-1: POST /v1/auth/signup with empty store returns 201, body `{user_id, tier:"member"}`,
/// and no set-cookie header (signup != implicit login).
#[tokio::test]
async fn ac1_signup_returns_201_with_member_tier_and_no_cookie() {
    // AC-1: valid signup -> 201 + {user_id:<uuid>, tier:"member"} + no Set-Cookie header.
    let state = make_state_with_empty_lookup();
    let resp = signup(
        state,
        r#"{"email":"newuser@example.com","password":"ValidPassw0rd!"}"#,
    )
    .await;

    assert_eq!(resp.status(), StatusCode::CREATED);

    // AC-1: signup must NOT set a session cookie.
    let has_cookie = resp
        .headers()
        .get_all("set-cookie")
        .iter()
        .any(|v| v.to_str().unwrap_or("").contains("archiviste_session="));
    assert!(!has_cookie, "signup must not set archiviste_session cookie");

    let body = body_json(resp).await;
    assert_eq!(body["tier"], "member", "tier must be 'member'");
    assert!(
        body["user_id"].as_str().is_some(),
        "user_id must be a string (UUID)"
    );
    // Verify user_id parses as a valid UUID.
    let uid = body["user_id"].as_str().unwrap();
    assert!(
        uuid::Uuid::parse_str(uid).is_ok(),
        "user_id must be a valid UUID, got: {uid}"
    );
}

// ---------------------------------------------------------------------------
// AC-1 / service unavailable: no UserLookup in state -> 503
// ---------------------------------------------------------------------------

/// AC-1 (failure mode): without DB pool / `user_lookup`, signup returns 503.
#[tokio::test]
async fn ac1_signup_without_db_returns_503() {
    // AC-1: signup requires DB (user_lookup). No pool -> 503 upstream_unavailable.
    let state = make_state_no_db();
    let resp = signup(
        state,
        r#"{"email":"any@example.com","password":"ValidPassw0rd!"}"#,
    )
    .await;
    assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
}

// ---------------------------------------------------------------------------
// AC-2: 409 email_taken when email already registered
// ---------------------------------------------------------------------------

/// AC-2: POST /v1/auth/signup with an already-registered email returns 409 `email_taken`.
#[tokio::test]
async fn ac2_signup_with_existing_email_returns_409_email_taken() {
    // AC-2: email already in store -> 409 + {error:"email_taken"}.
    let placeholder_hash =
        "$argon2id$v=19$m=19456,t=2,p=1$AAAAAAAAAAAAAAAAAAAAAA$AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
            .to_string();
    let lookup = Arc::new(InMemoryUserLookup::with_user(
        "taken@example.com",
        uuid::Uuid::new_v4(),
        placeholder_hash,
        "member",
    ));
    let creator = Arc::new(InMemorySessionCreator);
    let config = make_test_config("http://127.0.0.1:1");
    let state = Arc::new(AppState::new_with_mocks(config, lookup, creator).unwrap());

    let resp = signup(
        state,
        r#"{"email":"taken@example.com","password":"ValidPassw0rd!"}"#,
    )
    .await;

    assert_eq!(resp.status(), StatusCode::CONFLICT);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "email_taken");
}

// ---------------------------------------------------------------------------
// AC-3: validation errors return 400
// ---------------------------------------------------------------------------

/// AC-3: empty email -> 400 `invalid_request`.
#[tokio::test]
async fn ac3_signup_rejects_empty_email() {
    // AC-3: email="" fails regex -> 400 invalid_request.
    let state = make_state_with_empty_lookup();
    let resp = signup(state, r#"{"email":"","password":"ValidPassw0rd!"}"#).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "invalid_request");
}

/// AC-3: email without '@' -> 400 `invalid_request`.
#[tokio::test]
async fn ac3_signup_rejects_email_without_at() {
    // AC-3: email="noatsign" does not match ^[^\s@]+@[^\s@]+\.[^\s@]+$ -> 400.
    let state = make_state_with_empty_lookup();
    let resp = signup(state, r#"{"email":"noatsign","password":"ValidPassw0rd!"}"#).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "invalid_request");
}

/// AC-3: email with '@' but no dot in domain -> 400 `invalid_request`.
#[tokio::test]
async fn ac3_signup_rejects_email_without_dot() {
    // AC-3: email="user@nodot" fails regex (no dot after @) -> 400.
    let state = make_state_with_empty_lookup();
    let resp = signup(
        state,
        r#"{"email":"user@nodot","password":"ValidPassw0rd!"}"#,
    )
    .await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "invalid_request");
}

/// AC-3: email longer than 254 characters -> 400 `invalid_request`.
#[tokio::test]
async fn ac3_signup_rejects_email_too_long() {
    // AC-3: email > 254 chars (EMAIL_MAX_LEN=254) -> 400 invalid_request.
    // 244 local-part chars + "@example.com" (11) = 255 chars total.
    let local = "a".repeat(244);
    let over_email = format!("{local}@example.com");
    assert!(
        over_email.len() > 254,
        "test setup: email must exceed 254 chars"
    );

    let state = make_state_with_empty_lookup();
    let body_str = format!(r#"{{"email":"{over_email}","password":"ValidPassw0rd!"}}"#);
    let resp = signup(state, &body_str).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "invalid_request");
}

/// AC-3: password shorter than 12 characters -> 400 `invalid_request`.
#[tokio::test]
async fn ac3_signup_rejects_password_too_short() {
    // AC-3: password < 12 chars (PASSWORD_MIN_LEN=12) -> 400 invalid_request.
    let state = make_state_with_empty_lookup();
    let resp = signup(
        state,
        r#"{"email":"user@example.com","password":"short1!"}"#,
    )
    .await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "invalid_request");
}

/// AC-3: password longer than 128 characters -> 400 `invalid_request`.
#[tokio::test]
async fn ac3_signup_rejects_password_too_long() {
    // AC-3: password > 128 chars (PASSWORD_MAX_LEN=128) -> 400 invalid_request.
    let long_pw = "A1!".repeat(43); // 129 chars
    assert!(
        long_pw.len() > 128,
        "test setup: password must exceed 128 chars"
    );

    let state = make_state_with_empty_lookup();
    let body_str = format!(r#"{{"email":"user@example.com","password":"{long_pw}"}}"#);
    let resp = signup(state, &body_str).await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "invalid_request");
}

/// AC-3: password containing a null byte (U+0000 via JSON unicode escape) -> 400 `invalid_request`.
///
/// The JSON unicode escape sequence \u0000 (6 ASCII chars in source) decodes to the
/// single U+0000 codepoint. Using the escape avoids embedding a literal null byte
/// (written here as \\x00 in commentary) in the source file which would corrupt
/// git binary detection.
#[tokio::test]
async fn ac3_signup_rejects_email_with_null_byte() {
    // AC-3: password containing U+0000 (\\x00 via JSON escape \u0000) -> 400 invalid_request.
    let state = make_state_with_empty_lookup();
    // The JSON string "validpassword\u0000!" embeds a null byte; validate_credentials rejects it.
    let resp = signup(
        state,
        "{\"email\":\"user@example.com\",\"password\":\"validpassword\\u0000!\"}",
    )
    .await;
    assert_eq!(resp.status(), StatusCode::BAD_REQUEST);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "invalid_request");
}

// ---------------------------------------------------------------------------
// AC-17: Content-Type and body size enforcement
// ---------------------------------------------------------------------------

/// AC-17: POST /v1/auth/signup with Content-Type: text/plain -> 415 Unsupported Media Type.
#[tokio::test]
async fn ac17_signup_wrong_content_type_returns_415() {
    // AC-17: auth routes enforce Content-Type: application/json -> 415 on mismatch.
    let state = make_state_no_db();
    let app = router(state);
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/auth/signup")
                .header("content-type", "text/plain")
                .body(Body::from(
                    r#"{"email":"x@x.com","password":"ValidPassw0rd!"}"#,
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::UNSUPPORTED_MEDIA_TYPE);
}

/// AC-17: POST /v1/auth/signup with body > 4096 bytes -> 413 Payload Too Large.
#[tokio::test]
async fn ac17_signup_body_too_large_returns_413() {
    // AC-17: 4 KiB body limit on auth routes -> 413 on oversized body.
    let state = make_state_no_db();
    let app = router(state);
    let big_body = "x".repeat(5_000);
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/auth/signup")
                .header("content-type", "application/json")
                .body(Body::from(big_body))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::PAYLOAD_TOO_LARGE);
}
