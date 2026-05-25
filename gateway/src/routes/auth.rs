//! Auth route handlers: signup, login, logout — SEC-001 PR-b.
//!
//! # Route overview
//! - `POST /v1/auth/signup`  — create member account (AC-1, AC-2, AC-3)
//! - `POST /v1/auth/login`   — authenticate and issue JWT + cookie (AC-4..AC-7)
//! - `POST /v1/auth/logout`  — revoke session and clear cookie (AC-8)
//!
//! # Constraints (AC-15)
//! Signup always inserts `tier='member'` — the literal string is the only value
//! used. No runtime path promotes a user to `'author'`.

use axum::{
    extract::State,
    http::{HeaderValue, StatusCode},
    response::{IntoResponse, Response},
    Extension, Json,
};
use chrono::Utc;
use regex::Regex;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::{sync::Arc, sync::OnceLock};
use unicode_normalization::UnicodeNormalization;
use uuid::Uuid;

use crate::{
    auth::jwt,
    auth::{
        extractor::{AuthError, AuthUser},
        jwt::{Claims, JWT_ISSUER_AUDIENCE, JWT_TTL_SECS},
        password,
    },
    errors::ApiError,
    state::AppState,
    RequestId,
};

// ---------------------------------------------------------------------------
// Metric counter — AC-19
// ---------------------------------------------------------------------------

/// Counter for `auth_failures_total{event="invalid_credentials"}` (AC-19, phase 1).
pub static AUTH_FAILURES_INVALID_CREDENTIALS: std::sync::atomic::AtomicU64 =
    std::sync::atomic::AtomicU64::new(0);

/// Counter for `auth_failures_total{event="throttled"}` (AC-19, phase 1).
pub static AUTH_FAILURES_THROTTLED: std::sync::atomic::AtomicU64 =
    std::sync::atomic::AtomicU64::new(0);

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Cookie name for the session JWT (AC-4).
const SESSION_COOKIE: &str = "archiviste_session";

/// JWT TTL / cookie `Max-Age` (7 days in seconds, AC-4).
const COOKIE_MAX_AGE: i64 = JWT_TTL_SECS;

/// Email max length per RFC 5321 / AC-3.
const EMAIL_MAX_LEN: usize = 254;

/// Password minimum length (AC-3).
const PASSWORD_MIN_LEN: usize = 12;

/// Password maximum length (AC-3).
const PASSWORD_MAX_LEN: usize = 128;

// ---------------------------------------------------------------------------
// Request / response bodies
// ---------------------------------------------------------------------------

/// Request body shared by signup and login.
#[derive(Debug, Deserialize)]
pub struct AuthRequest {
    /// Email address.
    pub email: String,
    /// Plain-text password.
    pub password: String,
}

/// `POST /v1/auth/signup` success body (AC-1).
#[derive(Debug, Serialize)]
pub struct SignupResponse {
    /// Created user UUID.
    pub user_id: String,
    /// Always `"member"` (AC-1, AC-15).
    pub tier: &'static str,
}

/// `POST /v1/auth/login` success body (AC-4).
#[derive(Debug, Serialize)]
pub struct LoginResponse {
    /// Signed JWT.
    pub access_token: String,
    /// Always `"Bearer"` (AC-4).
    pub token_type: &'static str,
    /// Cookie/JWT lifetime in seconds (AC-4).
    pub expires_in: i64,
}

// ---------------------------------------------------------------------------
// Error bodies (uniform envelope)
// ---------------------------------------------------------------------------

#[derive(Serialize)]
struct AuthErrorBody {
    error: &'static str,
    request_id: String,
}

#[derive(Serialize)]
struct ThrottleErrorBody {
    error: &'static str,
    request_id: String,
    retry_after_seconds: u64,
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/// Compiled email validation regex (AC-3). Initialised once, reused for all requests.
///
/// # Panics
///
/// Panics on startup if the hardcoded regex literal is syntactically invalid —
/// a programming error, not a runtime condition.
fn email_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    #[allow(clippy::expect_used)]
    RE.get_or_init(|| {
        Regex::new(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
            .expect("email regex literal is always syntactically valid")
    })
}

/// Normalise an email: NFKC + trim + lowercase (AC-3).
fn normalise_email(raw: &str) -> String {
    raw.nfkc().collect::<String>().trim().to_lowercase()
}

/// Validate email and password; return `Err(Response)` on any violation (AC-3).
///
/// `Response` is an opaque type larger than a pointer; the `#[allow]` suppresses
/// the `clippy::result_large_err` lint which would otherwise fire because `axum::Response`
/// is inherently large and there is no cheaper alternative without allocating.
#[allow(clippy::result_large_err)]
fn validate_credentials(email: &str, password: &str, request_id: &str) -> Result<(), Response> {
    if email.len() > EMAIL_MAX_LEN || !email_regex().is_match(email) {
        return Err(invalid_request_response(request_id));
    }
    if password.len() < PASSWORD_MIN_LEN
        || password.len() > PASSWORD_MAX_LEN
        || password.contains('\0')
    {
        return Err(invalid_request_response(request_id));
    }
    Ok(())
}

fn invalid_request_response(request_id: &str) -> Response {
    (
        StatusCode::BAD_REQUEST,
        json_ct(),
        Json(AuthErrorBody {
            error: "invalid_request",
            request_id: request_id.to_string(),
        }),
    )
        .into_response()
}

fn upstream_unavailable_response(request_id: &str) -> Response {
    (
        StatusCode::SERVICE_UNAVAILABLE,
        Json(AuthErrorBody {
            error: "upstream_unavailable",
            request_id: request_id.to_string(),
        }),
    )
        .into_response()
}

fn internal_error_response(request_id: &str) -> Response {
    (
        StatusCode::INTERNAL_SERVER_ERROR,
        Json(AuthErrorBody {
            error: "internal",
            request_id: request_id.to_string(),
        }),
    )
        .into_response()
}

fn json_ct() -> [(axum::http::HeaderName, HeaderValue); 1] {
    [(
        axum::http::header::CONTENT_TYPE,
        HeaderValue::from_static("application/json; charset=utf-8"),
    )]
}

/// Build the `Set-Cookie` header value for `archiviste_session`.
///
/// # Panics
///
/// Panics if the constructed cookie string contains non-ASCII characters —
/// impossible because `SESSION_COOKIE` is a string literal, `max_age` is a
/// decimal integer, and the JWT token is base64url (ASCII-only).
#[must_use]
#[allow(clippy::expect_used)]
pub fn session_cookie(token: &str, max_age: i64) -> HeaderValue {
    let s = format!(
        "{SESSION_COOKIE}={token}; HttpOnly; Secure; SameSite=Lax; Path=/; Max-Age={max_age}"
    );
    HeaderValue::from_str(&s).expect("cookie string is always ASCII-safe")
}

/// SHA-256 hex of a string (for AC-18 structured logging — never log raw email).
fn sha256_hex(s: &str) -> String {
    format!("{:x}", Sha256::digest(s.as_bytes()))
}

// ---------------------------------------------------------------------------
// POST /v1/auth/signup — private helpers (M5 extract)
// ---------------------------------------------------------------------------

/// Build the 409 email-taken response (AC-2).
fn build_email_taken_response(request_id: &str, email_sha256: &str, latency_ms: u128) -> Response {
    tracing::warn!(
        event = "auth.signup.email_taken",
        email_sha256 = %email_sha256,
        request_id = %request_id,
        latency_ms = latency_ms,
    );
    (
        StatusCode::CONFLICT,
        json_ct(),
        Json(AuthErrorBody {
            error: "email_taken",
            request_id: request_id.to_string(),
        }),
    )
        .into_response()
}

/// Build the 201 signup success response (AC-1).
fn build_signup_success(
    user_id: Uuid,
    email_sha256: &str,
    request_id: &str,
    latency_ms: u128,
) -> Response {
    tracing::info!(
        event = "auth.signup.ok",
        email_sha256 = %email_sha256,
        tier = "member",
        request_id = %request_id,
        latency_ms = latency_ms,
    );
    (
        StatusCode::CREATED,
        json_ct(),
        Json(SignupResponse {
            user_id: user_id.to_string(),
            tier: "member",
        }),
    )
        .into_response()
}

// ---------------------------------------------------------------------------
// POST /v1/auth/login — private helpers (M5 extract)
// ---------------------------------------------------------------------------

/// Build the 401 invalid-credentials response (AC-6).
fn build_invalid_credentials_response(
    request_id: &str,
    email_sha256: &str,
    latency_ms: u128,
) -> Response {
    AUTH_FAILURES_INVALID_CREDENTIALS.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    tracing::warn!(
        event = "auth.login.invalid_credentials",
        email_sha256 = %email_sha256,
        request_id = %request_id,
        latency_ms = latency_ms,
    );
    (
        StatusCode::UNAUTHORIZED,
        json_ct(),
        Json(AuthErrorBody {
            error: "invalid_credentials",
            request_id: request_id.to_string(),
        }),
    )
        .into_response()
}

/// Build the 429 throttled response (AC-7).
fn build_throttled_response(
    request_id: &str,
    email_sha256: &str,
    retry_after: u64,
    latency_ms: u128,
) -> Response {
    AUTH_FAILURES_THROTTLED.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
    tracing::warn!(
        event = "auth.login.throttled",
        email_sha256 = %email_sha256,
        request_id = %request_id,
        latency_ms = latency_ms,
    );
    let mut resp = (
        StatusCode::TOO_MANY_REQUESTS,
        json_ct(),
        Json(ThrottleErrorBody {
            error: "login_throttled",
            request_id: request_id.to_string(),
            retry_after_seconds: retry_after,
        }),
    )
        .into_response();
    // `u64` → `HeaderValue` is infallible — integer values are always ASCII-safe.
    resp.headers_mut()
        .insert("retry-after", HeaderValue::from(retry_after));
    resp
}

/// All data needed to produce a login success response (N-3 fix — replaces 7-param fn).
struct LoginContext<'a> {
    state: &'a AppState,
    user_id: Uuid,
    tier: &'a str,
    sid: Uuid,
    request_id: &'a str,
    email_sha256: &'a str,
    latency_ms: u128,
}

/// Build JWT token from login context; returns `None` on signing failure.
fn build_jwt_token(ctx: &LoginContext<'_>) -> Option<String> {
    let now = Utc::now().timestamp();
    let claims = Claims {
        sub: ctx.user_id.to_string(),
        tier: ctx.tier.to_string(),
        sid: ctx.sid.to_string(),
        iat: now,
        exp: now + JWT_TTL_SECS,
        iss: JWT_ISSUER_AUDIENCE.to_string(),
        aud: JWT_ISSUER_AUDIENCE.to_string(),
    };
    jwt::sign(
        &claims,
        &ctx.state.config.jwt_ed25519_private_key_pem,
        &ctx.state.config.jwt_kid,
    )
    .ok()
}

/// Build the 200 login success response (JWT + cookie — AC-4, AC-5).
fn build_login_success(ctx: &LoginContext<'_>) -> Response {
    let Some(jwt_token) = build_jwt_token(ctx) else {
        return internal_error_response(ctx.request_id);
    };
    tracing::info!(
        event = "auth.login.ok",
        email_sha256 = %ctx.email_sha256,
        tier = %ctx.tier,
        request_id = %ctx.request_id,
        latency_ms = ctx.latency_ms,
    );
    let cookie = session_cookie(&jwt_token, COOKIE_MAX_AGE);
    let mut resp = (
        StatusCode::OK,
        json_ct(),
        Json(LoginResponse {
            access_token: jwt_token,
            token_type: "Bearer",
            expires_in: COOKIE_MAX_AGE,
        }),
    )
        .into_response();
    resp.headers_mut().append("set-cookie", cookie);
    resp
}

// ---------------------------------------------------------------------------
// POST /v1/auth/signup — private helpers (M5 extract)
// ---------------------------------------------------------------------------

/// Check email uniqueness with timing-safe dummy hash on collision (AC-2).
///
/// Returns `Ok(true)` if taken (with dummy hash already run), `Ok(false)` if free,
/// `Err(())` on DB failure.
async fn check_email_unique_timing_safe(
    lookup: &dyn crate::auth::user_lookup::UserLookup,
    email: &str,
    password: &str,
) -> Result<bool, ()> {
    let taken = lookup.email_is_taken(email).await.map_err(|_| ())?;
    if taken {
        password::verify_dummy(password);
    }
    Ok(taken)
}

// ---------------------------------------------------------------------------
// POST /v1/auth/signup (AC-1, AC-2, AC-3)
// ---------------------------------------------------------------------------

/// Handler for `POST /v1/auth/signup`.
///
/// Creates a `member` account. Does NOT create a session or set a cookie
/// (signup is not an implicit login — AC-1).
pub async fn signup(
    State(state): State<Arc<AppState>>,
    Extension(req_id): Extension<RequestId>,
    Json(body): Json<AuthRequest>,
) -> Response {
    let request_id = req_id.0.clone();
    let start = std::time::Instant::now();
    let email = normalise_email(&body.email);
    if let Err(resp) = validate_credentials(&email, &body.password, &request_id) {
        return resp;
    }
    let email_sha256 = sha256_hex(&email);
    let Some(lookup) = state.user_lookup.as_ref() else {
        return upstream_unavailable_response(&request_id);
    };
    match check_email_unique_timing_safe(lookup.as_ref(), &email, &body.password).await {
        Err(()) => return upstream_unavailable_response(&request_id),
        Ok(true) => {
            return build_email_taken_response(
                &request_id,
                &email_sha256,
                start.elapsed().as_millis(),
            );
        }
        Ok(false) => {}
    }
    let Ok(password_hash) = password::hash(&body.password) else {
        return internal_error_response(&request_id);
    };
    match lookup.create_member(&email, &password_hash).await {
        Err(_) => internal_error_response(&request_id),
        Ok(user_id) => build_signup_success(
            user_id,
            &email_sha256,
            &request_id,
            start.elapsed().as_millis(),
        ),
    }
}

// ---------------------------------------------------------------------------
// POST /v1/auth/login — private helpers (M5 extract)
// ---------------------------------------------------------------------------

/// Verify password against the stored hash, always running argon2id (AC-6 timing-safe).
///
/// Returns `(Some(user_id), Some(tier))` on success, `(None, None)` on failure.
/// `Err` signals a DB lookup failure.
async fn verify_credentials_with_timing(
    lookup: &dyn crate::auth::user_lookup::UserLookup,
    email: &str,
    password: &str,
) -> Result<(Option<uuid::Uuid>, Option<String>), ()> {
    let user_row = lookup.find_member(email).await.map_err(|_| ())?;

    Ok(if let Some((id, stored_hash, tier)) = user_row {
        let ok = password::verify(password, &stored_hash).unwrap_or(false);
        if ok {
            (Some(id), Some(tier))
        } else {
            (None, None)
        }
    } else {
        // AC-6: dummy argon2id so latency matches "found but wrong password".
        password::verify_dummy(password);
        (None, None)
    })
}

// ---------------------------------------------------------------------------
// POST /v1/auth/login (AC-4, AC-5, AC-6, AC-7)
// ---------------------------------------------------------------------------

/// Return `Some(429 response)` if `email` is currently throttled, else `None` (AC-7).
fn pre_login_throttle_check(
    state: &AppState,
    email: &str,
    email_sha256: &str,
    request_id: &str,
    latency_ms: u128,
) -> Option<Response> {
    state.throttle.is_throttled(email).map(|retry_after| {
        build_throttled_response(request_id, email_sha256, retry_after, latency_ms)
    })
}

/// Verify credentials and record failure on mismatch; return `Ok((user_id, tier))` or `Err(response)`.
///
/// `Err` carries either a 401 `invalid_credentials` response (bad password / unknown
/// email) or a 503 on DB failure.
#[allow(clippy::result_large_err)]
async fn do_login_credential_flow(
    state: &AppState,
    email: &str,
    email_sha256: &str,
    password: &str,
    request_id: &str,
    latency_ms: u128,
) -> Result<(Uuid, String), Response> {
    let Some(lookup) = state.user_lookup.as_ref() else {
        return Err(upstream_unavailable_response(request_id));
    };
    let Ok((uid_opt, tier_opt)) =
        verify_credentials_with_timing(lookup.as_ref(), email, password).await
    else {
        return Err(upstream_unavailable_response(request_id));
    };
    let Some(user_id) = uid_opt else {
        state.throttle.record_failure(email);
        return Err(build_invalid_credentials_response(
            request_id,
            email_sha256,
            latency_ms,
        ));
    };
    state.throttle.record_success(email);
    Ok((user_id, tier_opt.unwrap_or_else(|| "member".to_string())))
}

/// Create a session and build the success response; `Err(response)` on any failure.
///
/// Extracted from `login` to keep the handler body ≤ 40 lines (clean-code.md).
#[allow(clippy::result_large_err)]
async fn do_create_session_and_build_response(
    state: &AppState,
    user_id: Uuid,
    tier_str: &str,
    request_id: &str,
    email_sha256: &str,
    latency_ms: u128,
) -> Result<Response, Response> {
    let Some(creator) = state.session_creator.as_ref() else {
        return Err(upstream_unavailable_response(request_id));
    };
    let Ok((sid, _raw_token)) = creator.create(user_id).await else {
        return Err(upstream_unavailable_response(request_id));
    };
    Ok(build_login_success(&LoginContext {
        state,
        user_id,
        tier: tier_str,
        sid,
        request_id,
        email_sha256,
        latency_ms,
    }))
}

/// Handler for `POST /v1/auth/login`.
///
/// Verifies credentials, checks throttle, creates session, issues JWT + cookie.
pub async fn login(
    State(state): State<Arc<AppState>>,
    Extension(req_id): Extension<RequestId>,
    Json(body): Json<AuthRequest>,
) -> Response {
    let request_id = req_id.0.clone();
    let start = std::time::Instant::now();
    let email = normalise_email(&body.email);
    let email_sha256 = sha256_hex(&email);

    if let Some(resp) = pre_login_throttle_check(
        &state,
        &email,
        &email_sha256,
        &request_id,
        start.elapsed().as_millis(),
    ) {
        return resp;
    }

    let (user_id, tier_str) = match do_login_credential_flow(
        &state,
        &email,
        &email_sha256,
        &body.password,
        &request_id,
        start.elapsed().as_millis(),
    )
    .await
    {
        Ok(v) => v,
        Err(resp) => return resp,
    };

    do_create_session_and_build_response(
        &state,
        user_id,
        &tier_str,
        &request_id,
        &email_sha256,
        start.elapsed().as_millis(),
    )
    .await
    .unwrap_or_else(|e| e)
}

// ---------------------------------------------------------------------------
// POST /v1/auth/logout (AC-8)
// ---------------------------------------------------------------------------

/// Handler for `POST /v1/auth/logout`.
///
/// Requires valid authentication (`AuthUser` extractor).
/// Revokes the session and clears the session cookie (AC-8).
pub async fn logout(
    State(state): State<Arc<AppState>>,
    Extension(req_id): Extension<RequestId>,
    auth: Result<AuthUser, AuthError>,
) -> Response {
    let request_id = req_id.0.clone();

    // AC-8: logout requires an authenticated caller.
    let auth_user = match auth {
        Ok(u) => u,
        Err(e) => return e.into_response(),
    };

    let Some(revoker) = state.session_revoker.as_ref() else {
        return ApiError::UpstreamUnavailable.into_response();
    };

    if revoker.revoke(auth_user.sid).await.is_err() {
        return ApiError::UpstreamUnavailable.into_response();
    }

    tracing::info!(
        event = "auth.logout.ok",
        tier = %auth_user.tier,
        request_id = %request_id,
    );

    let clear_cookie = session_cookie("", 0);
    let mut resp = StatusCode::NO_CONTENT.into_response();
    resp.headers_mut().append("set-cookie", clear_cookie);
    resp
}
