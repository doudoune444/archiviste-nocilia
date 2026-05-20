//! Axum extractors for authenticated / anonymous identity — SEC-001 PR-a.
//!
//! `AuthUser` resolves the caller's identity from JWT (cookie or Authorization header).
//! Three resolution paths:
//!   1. Valid JWT + valid session → `AuthUser` with `tier=member|author` and `sid`.
//!   2. No JWT or invalid JWT → the extractor returns `Err(AuthError::InvalidToken)` (401).
//!   3. Valid JWT but session DB unreachable → `Err(AuthError::Upstream)` (503).
//!
//! The anonymous fingerprint path is handled by middleware (see `lib.rs`) and
//! attached as an `AnonIdentity` extension.  Handlers that accept anonymous
//! callers should use `AnonIdentity`, not `AuthUser`.
//!
//! Routes requiring `author` tier use the `RequireAuthor` extractor (returns 403
//! `author_required` for `member` or anonymous callers with a valid JWT).

use axum::{
    extract::FromRequestParts,
    http::{request::Parts, StatusCode},
    response::{IntoResponse, Response},
    Json,
};
use serde::Serialize;
use std::{fmt, sync::Arc};
use uuid::Uuid;

use crate::{
    auth::{
        jwt,
        sessions::{self, SessionError},
    },
    state::AppState,
};

// ---------------------------------------------------------------------------
// AC-19: auth failure counters
// ---------------------------------------------------------------------------

/// In-process counter for `auth_failures_total{event=invalid_token}` (AC-19).
///
/// Phase 1: atomic counter + `tracing::warn!` event (searchable in log aggregators).
/// A real OTel/Prometheus counter is wired in SEC-002 (multi-replica metrics export).
pub static AUTH_FAILURES_INVALID_TOKEN: std::sync::atomic::AtomicU64 =
    std::sync::atomic::AtomicU64::new(0);

// ---------------------------------------------------------------------------
// UserTier
// ---------------------------------------------------------------------------

/// The three identity tiers (AC-11).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum UserTier {
    /// Unauthenticated visitor identified by fingerprint.
    Anonymous,
    /// Registered member (email+password signup).
    Member,
    /// Content author (seed-only, no runtime promotion — AC-15).
    Author,
}

impl UserTier {
    /// String representation forwarded in `X-User-Tier` header (AC-14).
    #[must_use]
    pub fn as_str(self) -> &'static str {
        match self {
            Self::Anonymous => "anonymous",
            Self::Member => "member",
            Self::Author => "author",
        }
    }

    /// Parse from a JWT `tier` claim string.
    #[must_use]
    pub fn from_claim(s: &str) -> Option<Self> {
        match s {
            "member" => Some(Self::Member),
            "author" => Some(Self::Author),
            _ => None,
        }
    }
}

impl fmt::Display for UserTier {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(self.as_str())
    }
}

// ---------------------------------------------------------------------------
// AuthUser — resolved authenticated identity
// ---------------------------------------------------------------------------

/// Authenticated user extracted from a valid JWT (tier = `member` or `author`).
///
/// Use this extractor on routes that require authentication.  Returns 401 if
/// the JWT is absent, invalid, or the session is revoked/expired.
#[derive(Debug, Clone)]
pub struct AuthUser {
    /// The user's UUID (from JWT `sub` claim).
    pub user_id: Uuid,
    /// The user's tier.
    pub tier: UserTier,
    /// The session UUID (from JWT `sid` claim) — used for logout / revocation.
    pub sid: Uuid,
}

/// Error returned when extracting `AuthUser` fails.
#[derive(Debug)]
pub enum AuthError {
    /// JWT absent, malformed, or cryptographically invalid (AC-12).
    InvalidToken,
    /// JWT structurally valid but session is revoked / expired / missing (AC-13).
    SessionRevoked,
    /// Database unavailable during session check (AC-13 failure mode → 503).
    Upstream,
    /// Caller's tier is insufficient for this route (AC-16).
    AuthorRequired,
}

// ---------------------------------------------------------------------------
// Extractor impl
// ---------------------------------------------------------------------------

impl FromRequestParts<Arc<AppState>> for AuthUser {
    type Rejection = AuthError;

    async fn from_request_parts(
        parts: &mut Parts,
        state: &Arc<AppState>,
    ) -> Result<Self, Self::Rejection> {
        let token = extract_bearer_token(parts).ok_or(AuthError::InvalidToken)?;

        let claims =
            jwt::verify(&token, &state.config.jwt_ed25519_public_key_pem).map_err(|_| {
                // AC-19: emit structured event on JWT verification failure.
                // Counter `auth_failures_total{event=invalid_token}` — Phase 1 via tracing.
                // A real metrics counter (Prometheus / OpenTelemetry) is wired in SEC-002.
                tracing::warn!(event = "auth_failure", reason = "invalid_token");
                AUTH_FAILURES_INVALID_TOKEN.fetch_add(1, std::sync::atomic::Ordering::Relaxed);
                AuthError::InvalidToken
            })?;

        let user_id = Uuid::parse_str(&claims.sub).map_err(|_| AuthError::InvalidToken)?;
        let sid = Uuid::parse_str(&claims.sid).map_err(|_| AuthError::InvalidToken)?;
        let tier = UserTier::from_claim(&claims.tier).ok_or(AuthError::InvalidToken)?;

        // AC-13: server-side session check (no cache phase 1).
        // If no pool is configured (test environment), skip the check — session is
        // assumed valid. In production `run()` always provides a pool; absence is
        // a boot-time invariant, not a per-request runtime condition.
        if let Some(pool) = &state.db_pool {
            match sessions::check_session(pool, sid).await {
                Ok(()) => {}
                Err(SessionError::Revoked) => return Err(AuthError::SessionRevoked),
                Err(SessionError::Unavailable) => return Err(AuthError::Upstream),
            }
        }

        Ok(AuthUser { user_id, tier, sid })
    }
}

// ---------------------------------------------------------------------------
// RequireAuthor extractor — wraps AuthUser + tier check
// ---------------------------------------------------------------------------

/// Extractor that additionally requires `tier=author` (AC-16).
///
/// Returns 403 `author_required` if the caller is `member` or anonymous.
#[derive(Debug, Clone)]
pub struct RequireAuthor(pub AuthUser);

impl FromRequestParts<Arc<AppState>> for RequireAuthor {
    type Rejection = AuthError;

    async fn from_request_parts(
        parts: &mut Parts,
        state: &Arc<AppState>,
    ) -> Result<Self, Self::Rejection> {
        let auth = AuthUser::from_request_parts(parts, state).await?;
        if auth.tier != UserTier::Author {
            return Err(AuthError::AuthorRequired);
        }
        Ok(RequireAuthor(auth))
    }
}

// ---------------------------------------------------------------------------
// AnonIdentity — fingerprint-derived identity (anonymous callers)
// ---------------------------------------------------------------------------

/// Identity resolved for any caller (authenticated or anonymous).
///
/// Set by the `resolve_identity` middleware in `lib.rs` and attached as
/// a request extension before reaching handlers.
#[derive(Debug, Clone)]
pub struct AnonIdentity {
    /// Derived `user_id` (`UUIDv5` from fingerprint for anon; JWT sub for members).
    pub user_id: Uuid,
    /// Caller tier.
    pub tier: UserTier,
    /// Fingerprint hex (64 chars) for anonymous callers; `None` for members/authors.
    pub fingerprint: Option<String>,
}

// ---------------------------------------------------------------------------
// IntoResponse for AuthError
// ---------------------------------------------------------------------------

/// Error envelope for auth failures (AC-12 / AC-13 / AC-16).
#[derive(Serialize)]
struct AuthErrorBody {
    error: &'static str,
    request_id: String,
}

impl IntoResponse for AuthError {
    fn into_response(self) -> Response {
        let request_id = Uuid::new_v4().to_string();

        let (status, code) = match self {
            AuthError::InvalidToken => (StatusCode::UNAUTHORIZED, "invalid_token"),
            AuthError::SessionRevoked => (StatusCode::UNAUTHORIZED, "session_revoked"),
            AuthError::Upstream => (StatusCode::SERVICE_UNAVAILABLE, "upstream_unavailable"),
            AuthError::AuthorRequired => (StatusCode::FORBIDDEN, "author_required"),
        };

        (
            status,
            [(
                axum::http::header::CONTENT_TYPE,
                axum::http::HeaderValue::from_static("application/json; charset=utf-8"),
            )],
            Json(AuthErrorBody {
                error: code,
                request_id,
            }),
        )
            .into_response()
    }
}

// ---------------------------------------------------------------------------
// Cookie / header extraction helpers
// ---------------------------------------------------------------------------

/// Extract bearer token from `Authorization: Bearer <token>` header
/// or `archiviste_session` cookie (cookie takes priority per D-6).
fn extract_bearer_token(parts: &Parts) -> Option<String> {
    // Cookie-first (D-6: cookie prioritaire si les deux présents).
    if let Some(cookie_header) = parts.headers.get(axum::http::header::COOKIE) {
        if let Ok(cookie_str) = cookie_header.to_str() {
            for part in cookie_str.split(';') {
                let part = part.trim();
                if let Some(val) = part.strip_prefix("archiviste_session=") {
                    if !val.is_empty() {
                        return Some(val.to_string());
                    }
                }
            }
        }
    }

    // Fallback: Authorization: Bearer <token>.
    let auth_header = parts.headers.get(axum::http::header::AUTHORIZATION)?;
    let auth_str = auth_header.to_str().ok()?;
    auth_str.strip_prefix("Bearer ").map(str::to_string)
}
