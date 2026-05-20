//! Session lookup — SEC-001 PR-a (check only; create/revoke added in PR-b).
//!
//! Each authenticated request verifies the session is neither revoked nor
//! expired (AC-13). No cache phase 1 — single DB round-trip per request.
//! `security.md` §A07 forbids fail-open: Postgres unavailable → 503.

use sqlx::PgPool;
use uuid::Uuid;

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
