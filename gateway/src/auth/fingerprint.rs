//! Anonymous fingerprint computation — SEC-001 PR-a (AC-10, AC-21).
//!
//! Fingerprint = SHA-256(`<ip>|<user_agent>|<anon_cookie_uuid>`).
//! The `user_id` for anonymous requests is `UUIDv5(NIL namespace, fingerprint_hex)`.
//!
//! IP priority (AC-21):
//!   1. `CF-Connecting-IP` header (Cloudflare ingress, V1 production).
//!   2. `ConnectInfo<SocketAddr>` (dev local without Cloudflare).

use sha2::{Digest, Sha256};
use std::{fmt::Write as _, net::SocketAddr};
use uuid::Uuid;

/// Cookie name for the anonymous identity token (AC-10).
pub const ANON_COOKIE_NAME: &str = "archiviste_anon";

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Compute the 64-char hex fingerprint from the three identity components.
///
/// `anon_id` is the value of the `archiviste_anon` cookie (new or existing).
#[must_use]
pub fn compute_fingerprint(ip: &str, user_agent: &str, anon_id: &str) -> String {
    let input = format!("{ip}|{user_agent}|{anon_id}");
    let hash = Sha256::digest(input.as_bytes());
    encode_hex(hash.as_ref())
}

/// Derive the anonymous `user_id` (`UUIDv5`, NIL namespace) from the fingerprint.
#[must_use]
pub fn fingerprint_to_user_id(fingerprint_hex: &str) -> Uuid {
    Uuid::new_v5(&Uuid::NAMESPACE_DNS, fingerprint_hex.as_bytes())
}

/// Extract client IP from an Axum request.
///
/// Prefers `CF-Connecting-IP` header (AC-21). Falls back to the peer `SocketAddr`
/// from the `ConnectInfo` extension (always present when using `axum::serve`).
#[must_use]
pub fn extract_ip(headers: &axum::http::HeaderMap, connect_info: Option<&SocketAddr>) -> String {
    if let Some(cf_ip) = headers.get("cf-connecting-ip") {
        if let Ok(ip) = cf_ip.to_str() {
            return ip.to_string();
        }
    }
    connect_info.map_or_else(|| "unknown".to_string(), |addr| addr.ip().to_string())
}

/// Extract the `User-Agent` string (empty string if absent).
#[must_use]
pub fn extract_user_agent(headers: &axum::http::HeaderMap) -> &str {
    headers
        .get(axum::http::header::USER_AGENT)
        .and_then(|v| v.to_str().ok())
        .unwrap_or("")
}

/// Parse the `archiviste_anon` UUID from the `Cookie` header.
///
/// Returns `None` if the cookie is absent or cannot be parsed as a UUID
/// (caller must generate a new one in that case).
#[must_use]
pub fn parse_anon_cookie(headers: &axum::http::HeaderMap) -> Option<String> {
    let cookie_header = headers.get(axum::http::header::COOKIE)?.to_str().ok()?;
    for part in cookie_header.split(';') {
        let part = part.trim();
        if let Some(val) = part.strip_prefix(&format!("{ANON_COOKIE_NAME}=")) {
            // Validate it is a valid UUID to prevent fingerprint manipulation.
            if Uuid::parse_str(val).is_ok() {
                return Some(val.to_string());
            }
        }
    }
    None
}

// ---------------------------------------------------------------------------
// Private helpers — hex encoding
// ---------------------------------------------------------------------------

fn encode_hex(bytes: &[u8]) -> String {
    let mut out = String::with_capacity(bytes.len() * 2);
    for b in bytes {
        let _ = write!(out, "{b:02x}");
    }
    out
}
