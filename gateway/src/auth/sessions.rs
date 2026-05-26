//! Session CRUD — SEC-001 (PR-a: check; PR-b: create + revoke).
//!
//! Each authenticated request verifies the session is neither revoked nor
//! expired (AC-13). No cache phase 1 — single DB round-trip per request.
//! `security.md` §A07 forbids fail-open: Postgres unavailable → 503.
//!
//! Session tokens are 32 random bytes, stored as an argon2id hash (AC-4,
//! `security.md` §A07). The raw token is returned to the caller once and
//! is never persisted.
//!
//! `SessionCreator` and `SessionRevoker` traits allow test injection without
//! a real DB (M3/N-5 fix — reviewer request SEC-001 PR-b).

use argon2::password_hash::{rand_core::OsRng, rand_core::RngCore, PasswordHasher, SaltString};
use argon2::{Argon2, Params, Version};
use base64::engine::general_purpose::URL_SAFE_NO_PAD;
use base64::Engine as _;
use chrono::{Duration, Utc};
use sqlx::PgPool;
use std::pin::Pin;
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Shared future alias
// ---------------------------------------------------------------------------

type RevokeFuture<'a> =
    Pin<Box<dyn std::future::Future<Output = Result<(), SessionError>> + Send + 'a>>;

// ---------------------------------------------------------------------------
// SessionCreator trait (test injection for AC-4 handler happy path)
// ---------------------------------------------------------------------------

type SessionFuture<'a> =
    Pin<Box<dyn std::future::Future<Output = Result<(Uuid, String), SessionError>> + Send + 'a>>;

/// Abstraction over session creation — allows test injection without a real DB.
///
/// `SessionError::Unavailable` is returned on any failure.
pub trait SessionCreator: Send + Sync {
    /// Create a session for `user_id`, returning `(sid, raw_token)`.
    fn create(&self, user_id: Uuid) -> SessionFuture<'_>;
}

/// Production `SessionCreator` backed by a `PgPool`.
pub struct PgSessionCreator(pub PgPool);

impl SessionCreator for PgSessionCreator {
    fn create(&self, user_id: Uuid) -> SessionFuture<'_> {
        Box::pin(create_session(&self.0, user_id))
    }
}

// ---------------------------------------------------------------------------
// SessionRevoker trait (test injection for AC-8 handler happy path — N-5 fix)
// ---------------------------------------------------------------------------

/// Abstraction over session revocation — allows test injection without a real DB.
///
/// `SessionError::Unavailable` is returned on any failure.
pub trait SessionRevoker: Send + Sync {
    /// Revoke session `sid` by setting `revoked_at = NOW()`.
    fn revoke(&self, sid: Uuid) -> RevokeFuture<'_>;
}

/// Production `SessionRevoker` backed by a `PgPool`.
pub struct PgSessionRevoker(pub PgPool);

impl SessionRevoker for PgSessionRevoker {
    fn revoke(&self, sid: Uuid) -> RevokeFuture<'_> {
        Box::pin(revoke(&self.0, sid))
    }
}

// ---------------------------------------------------------------------------
// Error
// ---------------------------------------------------------------------------

/// Session verification error.
#[derive(Debug)]
pub enum SessionError {
    /// Session revoked, expired, or not found (AC-13).
    Revoked,
    /// Database unreachable (AC-13 / failure mode "Postgres indisponible").
    Unavailable,
}

// ---------------------------------------------------------------------------
// Row type for session check
// ---------------------------------------------------------------------------

/// Minimal session row returned by the check query.
#[derive(sqlx::FromRow)]
struct SessionStatus {
    is_revoked: bool,
    is_expired: bool,
}

// ---------------------------------------------------------------------------
// Check
// ---------------------------------------------------------------------------

// ---------------------------------------------------------------------------
// Create
// ---------------------------------------------------------------------------

/// Session token length in bytes (32 bytes = 256 bits of entropy, AC-4 / security.md §A07).
const TOKEN_BYTES: usize = 32;

/// Argon2id parameters for hashing the raw session token (AC-4 / `security.md` §A07).
///
/// Identical to the password-hashing parameters in `password.rs`.
///
/// # Panics
///
/// Panics if compile-time constants produce invalid `Params` — a programming error.
#[allow(clippy::expect_used)]
fn session_argon2() -> Argon2<'static> {
    let params = Params::new(19_456, 2, 1, None)
        .expect("Argon2 params are compile-time constants and always valid");
    Argon2::new(argon2::Algorithm::Argon2id, Version::V0x13, params)
}

/// Create a new session row for `user_id`.
///
/// Returns `(sid, raw_token)`:
/// - `sid` — the `sessions.id` UUID (to embed in the JWT `sid` claim).
/// - `raw_token` — URL-safe base64 of the raw 32-byte token. Set as the
///   `archiviste_session` cookie value. Never persisted.
///
/// The `token_hash` (argon2id of the raw token) is stored in the DB.
///
/// # Errors
///
/// Returns `SessionError::Unavailable` if the DB insert fails.
pub async fn create_session(pool: &PgPool, user_id: Uuid) -> Result<(Uuid, String), SessionError> {
    // 32 random bytes → URL-safe base64 (no padding) for the cookie value.
    let mut raw_bytes = [0u8; TOKEN_BYTES];
    OsRng.fill_bytes(&mut raw_bytes);
    let raw_token = URL_SAFE_NO_PAD.encode(raw_bytes);

    // Argon2id hash of the token for storage.
    let salt = SaltString::generate(&mut OsRng);
    let token_hash = session_argon2()
        .hash_password(raw_token.as_bytes(), &salt)
        .map(|h| h.to_string())
        .map_err(|_| SessionError::Unavailable)?;

    let sid = Uuid::new_v4();
    let expires_at = Utc::now() + Duration::seconds(7 * 24 * 60 * 60);

    sqlx::query(
        r"
        INSERT INTO sessions (id, user_id, token_hash, expires_at)
        VALUES ($1, $2, $3, $4)
        ",
    )
    .bind(sid)
    .bind(user_id)
    .bind(&token_hash)
    .bind(expires_at)
    .execute(pool)
    .await
    .map_err(|_| SessionError::Unavailable)?;

    Ok((sid, raw_token))
}

// ---------------------------------------------------------------------------
// Revoke
// ---------------------------------------------------------------------------

/// Mark session `sid` as revoked by setting `revoked_at = NOW()` (AC-8).
///
/// # Errors
///
/// Returns `SessionError::Unavailable` if the DB update fails.
pub async fn revoke(pool: &PgPool, sid: Uuid) -> Result<(), SessionError> {
    sqlx::query(
        r"
        UPDATE sessions SET revoked_at = NOW() WHERE id = $1
        ",
    )
    .bind(sid)
    .execute(pool)
    .await
    .map_err(|_| SessionError::Unavailable)?;
    Ok(())
}

// ---------------------------------------------------------------------------
// Check
// ---------------------------------------------------------------------------

/// Verify that session `sid` exists, is not revoked, and has not expired.
///
/// Returns `Ok(())` on success. Any failure returns a typed `SessionError`
/// so the extractor can map to the correct HTTP response.
///
/// # Errors
///
/// - `SessionError::Revoked` if `revoked_at IS NOT NULL` or `expires_at < NOW()` or row absent.
/// - `SessionError::Unavailable` if the database query fails.
pub async fn check_session(pool: &PgPool, sid: Uuid) -> Result<(), SessionError> {
    // Single indexed PK lookup — latency target < 2 ms (AC performance SLO).
    // Uses sqlx::query_as for runtime type safety without requiring offline cache.
    let row: Option<SessionStatus> = sqlx::query_as(
        r"
        SELECT
            (revoked_at IS NOT NULL) AS is_revoked,
            (expires_at < NOW())     AS is_expired
        FROM sessions
        WHERE id = $1
        ",
    )
    .bind(sid)
    .fetch_optional(pool)
    .await
    .map_err(|_| SessionError::Unavailable)?;

    match row {
        None => Err(SessionError::Revoked),
        Some(r) if r.is_revoked || r.is_expired => Err(SessionError::Revoked),
        Some(_) => Ok(()),
    }
}
