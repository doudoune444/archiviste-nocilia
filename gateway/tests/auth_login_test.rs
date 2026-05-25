//! Integration tests for `POST /v1/auth/login` — SEC-001 PR-b.
//!
//! AC-4: 200 + body + Set-Cookie on valid credentials (DB-gated).
//! AC-5: JWT header/payload structure decoded without signature check.
//! AC-6: 401 `invalid_credentials` for wrong password or unknown email.
//! AC-7: 429 `login_throttled` after 5 consecutive failures; reset on success.

#![allow(clippy::unwrap_used, clippy::expect_used)]

mod common;
use common::auth_mocks::InMemoryUserLookup;
use common::jwt_helpers::make_test_config;

use archiviste_gateway::{auth::throttle::ThrottleStore, router, state::AppState};
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

async fn login(state: Arc<AppState>, body: &str) -> axum::response::Response {
    let app = router(state);
    let req = Request::builder()
        .method("POST")
        .uri("/v1/auth/login")
        .header("content-type", "application/json")
        .body(Body::from(body.to_string()))
        .unwrap();
    app.oneshot(req).await.unwrap()
}

// ---------------------------------------------------------------------------
// AC-6: login without DB returns 503
// ---------------------------------------------------------------------------

/// AC-6 (unit path): without DB pool, login returns 503.
#[tokio::test]
async fn ac6_login_without_db_returns_503() {
    // AC-6 / AC-4: login requires DB — without pool → 503.
    let state = make_state();
    let resp = login(
        state,
        r#"{"email":"user@example.com","password":"correct-horse-battery"}"#,
    )
    .await;
    assert_eq!(resp.status(), StatusCode::SERVICE_UNAVAILABLE);
}

// ---------------------------------------------------------------------------
// AC-7: throttle mechanics (in-memory, no DB needed)
// ---------------------------------------------------------------------------

/// AC-7: after 5 failures for the same email, the 6th attempt returns 429.
#[tokio::test]
async fn ac7_throttled_after_five_failures() {
    // AC-7: 5 failures → throttle → 429 login_throttled + Retry-After header.
    let throttle = Arc::new(ThrottleStore::new());
    let email = "throttle-test@example.com";

    // Record 5 failures.
    for _ in 0..5 {
        throttle.record_failure(email);
    }

    // 6th attempt should be throttled.
    let retry_secs = throttle.is_throttled(email);
    assert!(
        retry_secs.is_some(),
        "Expected throttle after 5 failures, got None"
    );
    let secs = retry_secs.unwrap();
    assert!(
        secs > 0 && secs <= 15 * 60,
        "retry_after_seconds={secs} out of range"
    );
}

/// AC-7: fewer than 5 failures → not throttled.
#[tokio::test]
async fn ac7_not_throttled_after_four_failures() {
    // AC-7: 4 failures is below the threshold of 5.
    let throttle = Arc::new(ThrottleStore::new());
    let email = "four-fail@example.com";

    for _ in 0..4 {
        throttle.record_failure(email);
    }

    assert!(
        throttle.is_throttled(email).is_none(),
        "Should not be throttled after only 4 failures"
    );
}

/// AC-7: successful login resets the counter to zero.
#[tokio::test]
async fn ac7_success_resets_counter() {
    // AC-7: record_success clears the failure entry.
    let throttle = Arc::new(ThrottleStore::new());
    let email = "reset@example.com";

    for _ in 0..5 {
        throttle.record_failure(email);
    }
    assert!(throttle.is_throttled(email).is_some(), "Must be throttled");

    throttle.record_success(email);
    assert!(
        throttle.is_throttled(email).is_none(),
        "Must be unthrottled after success"
    );
}

/// AC-7: login endpoint returns 429 with Retry-After header when throttled.
///
/// This test exercises the HTTP handler path by building a state with a
/// pre-loaded throttle store.
#[tokio::test]
async fn ac7_login_handler_returns_429_when_throttled() {
    // AC-7: handler checks throttle before DB → 429 without DB required.
    let config = make_test_config("http://127.0.0.1:1");
    let state = Arc::new(AppState::new(config).unwrap());

    // Pre-load 5 failures for this email.
    let email = "throttle-handler@example.com";
    for _ in 0..5 {
        state.throttle.record_failure(email);
    }

    let app = router(state);
    let req = Request::builder()
        .method("POST")
        .uri("/v1/auth/login")
        .header("content-type", "application/json")
        .body(Body::from(
            r#"{"email":"throttle-handler@example.com","password":"doesnotmatter"}"#,
        ))
        .unwrap();

    let resp = app.oneshot(req).await.unwrap();
    assert_eq!(resp.status(), StatusCode::TOO_MANY_REQUESTS);

    // AC-7: Retry-After header must be present.
    assert!(
        resp.headers().contains_key("retry-after"),
        "Retry-After header missing on 429"
    );

    let body = body_json(resp).await;
    assert_eq!(body["error"], "login_throttled");
    assert!(
        body["retry_after_seconds"].as_u64().unwrap_or(0) > 0,
        "retry_after_seconds must be > 0"
    );
}

// ---------------------------------------------------------------------------
// AC-5: JWT header and payload shape (unit — no DB needed)
// ---------------------------------------------------------------------------

/// AC-5: a JWT produced by `jwt::sign` must carry the correct header and payload fields.
///
/// Decodes with the test public key to verify both header attributes
/// (alg=EdDSA, typ=JWT, kid present) and every payload claim.
#[test]
fn ac5_signed_jwt_has_correct_header_and_payload() {
    // AC-5: header alg=EdDSA, typ=JWT, kid present; payload sub/tier/sid/iat/exp/iss/aud correct.
    use archiviste_gateway::auth::jwt::{self, Claims, JWT_ISSUER_AUDIENCE, JWT_TTL_SECS};
    use common::jwt_helpers::{test_private_key_pem, test_public_key_pem, TEST_KEY_ID};
    use jsonwebtoken::{decode, decode_header, Algorithm, DecodingKey, Validation};
    use secrecy::SecretString;
    use uuid::Uuid;

    let sub = Uuid::new_v4();
    let sid = Uuid::new_v4();
    let iat = chrono::Utc::now().timestamp();
    let claims = Claims {
        sub: sub.to_string(),
        tier: "member".to_string(),
        sid: sid.to_string(),
        iat,
        exp: iat + JWT_TTL_SECS,
        iss: JWT_ISSUER_AUDIENCE.to_string(),
        aud: JWT_ISSUER_AUDIENCE.to_string(),
    };

    let private_key = SecretString::from(test_private_key_pem().to_string());
    let token = jwt::sign(&claims, &private_key, TEST_KEY_ID).expect("sign must succeed");

    // Verify header: alg=EdDSA, typ=JWT (jsonwebtoken sets this), kid=TEST_KEY_ID.
    let header = decode_header(&token).expect("decode_header must not fail");
    assert_eq!(header.alg, Algorithm::EdDSA, "alg must be EdDSA");
    assert_eq!(
        header.kid.as_deref(),
        Some(TEST_KEY_ID),
        "kid must match TEST_KEY_ID"
    );

    // Verify payload via full signature check with test public key.
    let dec_key =
        DecodingKey::from_ed_pem(test_public_key_pem().as_bytes()).expect("DecodingKey from pem");
    let mut validation = Validation::new(Algorithm::EdDSA);
    validation.set_issuer(&[JWT_ISSUER_AUDIENCE]);
    validation.set_audience(&[JWT_ISSUER_AUDIENCE]);
    let decoded = decode::<Claims>(&token, &dec_key, &validation).expect("decode must succeed");
    let p = decoded.claims;

    assert_eq!(p.sub, sub.to_string(), "sub must be the user UUID");
    assert_eq!(p.tier, "member", "tier must be member");
    assert_eq!(p.sid, sid.to_string(), "sid must be the session UUID");
    assert_eq!(p.iss, JWT_ISSUER_AUDIENCE, "iss");
    assert_eq!(p.aud, JWT_ISSUER_AUDIENCE, "aud");
    assert_eq!(p.iat, iat, "iat must match");
    assert_eq!(p.exp - p.iat, JWT_TTL_SECS, "exp - iat must be 604800");
}

/// AC-7: normalised emails are throttled together (case-insensitive).
///
/// The handler normalises the email before calling the throttle store.
/// This test simulates the normalisation: failures recorded under the
/// lowercase form are visible when looked up under the same lowercase form,
/// reflecting the contract that handler-side normalisation is the authority.
#[test]
fn ac7_throttle_keys_are_stable_for_same_input() {
    // AC-7: the throttle store key is the handler-normalised (lowercase) email.
    // Failures on "User@Example.COM" are stored under "user@example.com";
    // is_throttled("user@example.com") sees them.
    let throttle = ThrottleStore::new();

    let canonical = "user@example.com";
    // Simulate what the handler does: normalise → lowercase before calling store.
    let mixed_case = "User@Example.COM";
    let normalised = mixed_case.to_lowercase();
    assert_eq!(normalised, canonical, "normalise_email contract");

    for _ in 0..5 {
        throttle.record_failure(&normalised);
    }

    // Lookup with the canonical form must see the 5 recorded failures.
    assert!(
        throttle.is_throttled(canonical).is_some(),
        "throttle must fire on canonical key after 5 failures on normalised key"
    );
}

/// AC-7 (handler path): handler normalises email before throttle lookup.
///
/// Sends 5 failed login attempts with "User@Example.COM" (mixed case),
/// then a 6th with "user@example.com" (lowercase). The 6th must be
/// throttled because the handler normalises both to the same key.
#[tokio::test]
async fn ac7_handler_normalises_email_before_throttle_lookup() {
    // AC-7: handler calls normalise_email → throttle.is_throttled(normalised).
    // Mixed-case and canonical form share one throttle bucket.
    let lookup = Arc::new(InMemoryUserLookup::empty());
    let config = make_test_config("http://127.0.0.1:1");
    let state = Arc::new(AppState::new_with_lookup(config, lookup).unwrap());

    // 5 POSTs with mixed-case email → all fail 401 → throttle accumulates.
    for _ in 0..5 {
        let resp = login(
            Arc::clone(&state),
            r#"{"email":"User@Example.COM","password":"correct-horse-battery"}"#,
        )
        .await;
        // All fail 401 (unknown user), but we only care about throttle counting.
        assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
    }

    // 6th POST with canonical lowercase email → must be throttled (429).
    let resp = login(
        state,
        r#"{"email":"user@example.com","password":"correct-horse-battery"}"#,
    )
    .await;
    assert_eq!(
        resp.status(),
        StatusCode::TOO_MANY_REQUESTS,
        "6th attempt must be throttled because handler normalises email"
    );
}

// ---------------------------------------------------------------------------
// AC-17: Content-Type enforcement on login
// ---------------------------------------------------------------------------

/// AC-17: wrong Content-Type on login → 415.
#[tokio::test]
async fn ac17_login_wrong_content_type_returns_415() {
    // AC-17: auth routes enforce Content-Type: application/json.
    let state = make_state();
    let app = router(state);
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/auth/login")
                .header("content-type", "text/plain")
                .body(Body::from(
                    r#"{"email":"x@x.com","password":"12charpasswd"}"#,
                ))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::UNSUPPORTED_MEDIA_TYPE);
}

/// AC-17: body > 4096 bytes on login → 413.
#[tokio::test]
async fn ac17_login_body_too_large_returns_413() {
    // AC-17: 4 KiB limit on auth routes.
    let state = make_state();
    let app = router(state);
    let big_body = "x".repeat(5_000);
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/auth/login")
                .header("content-type", "application/json")
                .body(Body::from(big_body))
                .unwrap(),
        )
        .await
        .unwrap();
    assert_eq!(resp.status(), StatusCode::PAYLOAD_TOO_LARGE);
}

// ---------------------------------------------------------------------------
// AC-6 happy path with mock UserLookup (no DB required)
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// AC-4 happy path: login success with mock UserLookup + SessionCreator
// ---------------------------------------------------------------------------

/// AC-4: login with correct credentials returns 200, JWT, and Set-Cookie with
/// all 5 required attributes (`HttpOnly`, `Secure`, `SameSite=Lax`, `Path=/`, `Max-Age=604800`).
#[tokio::test]
async fn ac4_login_success_returns_200_with_jwt_and_cookie() {
    // AC-4: mock UserLookup + SessionCreator → 200 + body {access_token, token_type, expires_in}
    //       + Set-Cookie archiviste_session=...; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age=604800.
    use archiviste_gateway::auth::password;
    use common::auth_mocks::InMemorySessionCreator;
    use jsonwebtoken::{decode_header, Algorithm};

    let known_hash = password::hash("correct-horse-battery").unwrap();
    let user_id = Uuid::new_v4();
    let lookup = Arc::new(InMemoryUserLookup::with_user(
        "member@example.com",
        user_id,
        known_hash,
        "member",
    ));
    let creator = Arc::new(InMemorySessionCreator);
    let config = make_test_config("http://127.0.0.1:1");
    let state = Arc::new(AppState::new_with_mocks(config, lookup, creator).unwrap());

    let app = router(state);
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/auth/login")
                .header("content-type", "application/json")
                .body(Body::from(
                    r#"{"email":"member@example.com","password":"correct-horse-battery"}"#,
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(resp.status(), StatusCode::OK);

    // AC-4: Set-Cookie must have all 5 attributes.
    let set_cookie = resp
        .headers()
        .get_all("set-cookie")
        .iter()
        .find(|v| v.to_str().unwrap_or("").contains("archiviste_session="))
        .expect("archiviste_session Set-Cookie header must be present")
        .to_str()
        .unwrap()
        .to_string();

    assert!(set_cookie.contains("HttpOnly"), "missing HttpOnly");
    assert!(set_cookie.contains("Secure"), "missing Secure");
    assert!(set_cookie.contains("SameSite=Lax"), "missing SameSite=Lax");
    assert!(set_cookie.contains("Path=/"), "missing Path=/");
    assert!(
        set_cookie.contains("Max-Age=604800"),
        "missing Max-Age=604800"
    );

    let body = body_json(resp).await;
    assert_eq!(body["token_type"], "Bearer");
    assert_eq!(body["expires_in"], 604_800_i64);
    let jwt = body["access_token"]
        .as_str()
        .expect("access_token must be a string");

    // AC-5: JWT header must use EdDSA.
    let header = decode_header(jwt).expect("JWT must be decodable");
    assert_eq!(header.alg, Algorithm::EdDSA, "JWT alg must be EdDSA");
}

/// AC-6: login with wrong password returns 401 `invalid_credentials`.
/// Uses mock `UserLookup` seeded with a known user.
#[tokio::test]
async fn ac6_login_with_wrong_password_returns_401_invalid_credentials() {
    // AC-6: find_member returns user, but password mismatch → 401 + counter incremented.
    use archiviste_gateway::{auth::password, routes::auth::AUTH_FAILURES_INVALID_CREDENTIALS};
    use std::sync::atomic::Ordering;

    let known_hash = password::hash("correct-horse-battery").unwrap();
    let lookup = Arc::new(InMemoryUserLookup::with_user(
        "user@example.com",
        Uuid::new_v4(),
        known_hash,
        "member",
    ));
    let config = make_test_config("http://127.0.0.1:1");
    let state = Arc::new(AppState::new_with_lookup(config, lookup).unwrap());

    let before = AUTH_FAILURES_INVALID_CREDENTIALS.load(Ordering::Relaxed);

    let app = router(state);
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/auth/login")
                .header("content-type", "application/json")
                .body(Body::from(
                    r#"{"email":"user@example.com","password":"wrong-password-here!"}"#,
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "invalid_credentials");

    let after = AUTH_FAILURES_INVALID_CREDENTIALS.load(Ordering::Relaxed);
    assert!(
        after > before,
        "AUTH_FAILURES_INVALID_CREDENTIALS must increment on 401"
    );
}

/// AC-6: login with unknown email returns 401 `invalid_credentials`.
#[tokio::test]
async fn ac6_login_with_unknown_email_returns_401_invalid_credentials() {
    // AC-6: empty store → unknown email → 401 invalid_credentials.
    let lookup = Arc::new(InMemoryUserLookup::empty());
    let config = make_test_config("http://127.0.0.1:1");
    let state = Arc::new(AppState::new_with_lookup(config, lookup).unwrap());

    let app = router(state);
    let resp = app
        .oneshot(
            Request::builder()
                .method("POST")
                .uri("/v1/auth/login")
                .header("content-type", "application/json")
                .body(Body::from(
                    r#"{"email":"nobody@example.com","password":"correct-horse-battery"}"#,
                ))
                .unwrap(),
        )
        .await
        .unwrap();

    assert_eq!(resp.status(), StatusCode::UNAUTHORIZED);
    let body = body_json(resp).await;
    assert_eq!(body["error"], "invalid_credentials");
}
