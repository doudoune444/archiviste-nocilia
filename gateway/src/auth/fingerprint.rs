//! Anonymous identity derivation — IDN-001 (cookie-dominant identity).
//!
//! The anonymous `user_id` is derived **solely** from the validated
//! `archiviste_anon` cookie UUID.  IP address and User-Agent are no longer
//! part of the identity formula so that the same cookie always yields the
//! same `user_id` regardless of network or browser changes.
//!
//! Derivation formula:
//!   `user_id = UUIDv5(NIL_namespace, cookie_uuid.as_bytes())`
//!
//! Why `UUIDv5` over the raw cookie bytes rather than returning the cookie UUID
//! directly: `UUIDv5` keeps `user_id` in the UUID address space while making it
//! structurally distinct from the cookie value itself, preventing confusion
//! between the two values in logs and DB rows.  The mapping is bijective for
//! valid cookie UUIDs so stability is preserved.
//!
//! Cookie name `archiviste_anon` (`UUIDv4`, `Max-Age` 31536000, `HttpOnly`, Secure,
//! `SameSite=Lax`).  A missing or invalid cookie causes the caller to receive a
//! fresh `UUIDv4` cookie; the derived `user_id` is computed from that fresh value.

use uuid::Uuid;

/// Cookie name for the anonymous identity token (AC-10).
pub const ANON_COOKIE_NAME: &str = "archiviste_anon";

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Derive the anonymous `user_id` from the validated cookie UUID.
///
/// `cookie_uuid` is the validated value of the `archiviste_anon` cookie (either
/// an existing UUID from the request or a freshly generated one).
///
/// Formula: `UUIDv5(NIL_namespace, cookie_uuid.as_bytes())`
///
/// The NIL namespace (`Uuid::nil()`, all-zero) is required by the original
/// SEC-001 AC-10 contract ("namespace = NIL" per RFC 4122 §4.3). Using any
/// other namespace would break stored anonymous `user_id`s for this derivation
/// path.
#[must_use]
pub fn cookie_uuid_to_user_id(cookie_uuid: &Uuid) -> Uuid {
    Uuid::new_v5(&Uuid::nil(), cookie_uuid.as_bytes())
}

/// Parse the `archiviste_anon` UUID from the `Cookie` header.
///
/// Returns `None` if the cookie is absent or cannot be parsed as a UUID
/// (caller must generate a new one in that case).
#[must_use]
pub fn parse_anon_cookie(headers: &axum::http::HeaderMap) -> Option<Uuid> {
    let cookie_header = headers.get(axum::http::header::COOKIE)?.to_str().ok()?;
    for part in cookie_header.split(';') {
        let part = part.trim();
        if let Some(val) = part.strip_prefix(&format!("{ANON_COOKIE_NAME}=")) {
            // Validate it is a well-formed UUID to prevent identity manipulation.
            if let Ok(uuid) = Uuid::parse_str(val) {
                return Some(uuid);
            }
        }
    }
    None
}
