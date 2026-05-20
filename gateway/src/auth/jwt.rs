//! JWT `EdDSA` (Ed25519) verification — SEC-001 PR-a.
//!
//! Only `verify()` is shipped in PR-a. `sign()` is added in PR-b (requires the
//! private key that PR-a does not load).
//!
//! # Security
//! - `alg` is pinned to `EdDSA`. Any other value (including `none`, `HS256`, `RS256`)
//!   causes an immediate rejection — no fallback (AC-12, `security.md` §A02).
//! - `iss` and `aud` are validated against the literal `"archiviste-gateway"`.
//! - `exp` / `iat` are verified by `jsonwebtoken` with a 60-second leeway for `iat`.
//! - Unknown `kid` returns `invalid_token` (AC-12).

use jsonwebtoken::{DecodingKey, Validation};
use serde::{Deserialize, Serialize};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Expected `iss` / `aud` claim value (AC-5).
pub const JWT_ISSUER_AUDIENCE: &str = "archiviste-gateway";

/// Maximum future drift allowed for `iat` (seconds).
const IAT_LEEWAY_SECS: u64 = 60;

// ---------------------------------------------------------------------------
// Claims
// ---------------------------------------------------------------------------

/// JWT payload claims (AC-5).
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Claims {
    /// Subject: user UUID.
    pub sub: String,
    /// User tier (`member` or `author`).
    pub tier: String,
    /// Session UUID (server-side session row — AC-13).
    pub sid: String,
    /// Issued-at (Unix timestamp).
    pub iat: i64,
    /// Expiry (Unix timestamp).
    pub exp: i64,
    /// Issuer.
    pub iss: String,
    /// Audience.
    pub aud: String,
}

// ---------------------------------------------------------------------------
// Verification
// ---------------------------------------------------------------------------

/// Verify a JWT string against the provided Ed25519 public key PEM.
///
/// Returns parsed `Claims` on success.  Any structural, cryptographic, or
/// claims-level failure returns `Err(JwtError)`.
///
/// # Errors
///
/// Returns `JwtError::Invalid` on any verification failure.
pub fn verify(token: &str, public_key_pem: &str) -> Result<Claims, JwtError> {
    // Reject trivially non-JWT inputs early (no 3-segment dot structure).
    if token.split('.').count() != 3 {
        return Err(JwtError::Invalid);
    }

    // Decode header to pin alg before full decode (prevents alg confusion).
    let header = jsonwebtoken::decode_header(token).map_err(|_| JwtError::Invalid)?;
    if header.alg != jsonwebtoken::Algorithm::EdDSA {
        return Err(JwtError::Invalid);
    }

    let decoding_key =
        DecodingKey::from_ed_pem(public_key_pem.as_bytes()).map_err(|_| JwtError::Invalid)?;

    let mut validation = Validation::new(jsonwebtoken::Algorithm::EdDSA);
    validation.set_issuer(&[JWT_ISSUER_AUDIENCE]);
    validation.set_audience(&[JWT_ISSUER_AUDIENCE]);
    // Allow 60-second future-iat drift (AC-12 "iat futur > 60s rejeté").
    validation.leeway = IAT_LEEWAY_SECS;
    // Ensure exp is required.
    validation.validate_exp = true;

    let data = jsonwebtoken::decode::<Claims>(token, &decoding_key, &validation)
        .map_err(|_| JwtError::Invalid)?;

    Ok(data.claims)
}

// ---------------------------------------------------------------------------
// Error
// ---------------------------------------------------------------------------

/// JWT verification error (opaque — AC-12 forbids distinguishing failure reasons).
#[derive(Debug)]
pub enum JwtError {
    /// Any verification failure (alg mismatch, bad sig, expired, wrong iss/aud, etc.).
    Invalid,
}
