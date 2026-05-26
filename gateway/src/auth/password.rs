//! Argon2id password hashing and verification — SEC-001 PR-b.
//!
//! Parameters (AC-1, `security.md` §A02):
//!   - Algorithm  : `argon2id`
//!   - Memory     : 19 456 KiB (= 19 MiB)
//!   - Iterations : 2
//!   - Parallelism: 1
//!   - Salt       : 16 bytes, `OsRng`
//!
//! Timing-safe: `argon2::verify_encoded` runs in constant time (no early-out).
//! For email-taken (AC-2) and unknown-email (AC-6) paths a dummy reference hash
//! is computed once at startup so verification always runs argon2id even when
//! no real hash exists.

use argon2::{
    password_hash::{rand_core::OsRng, PasswordHash, PasswordHasher, PasswordVerifier, SaltString},
    Argon2, Params, Version,
};
use std::sync::OnceLock;

// ---------------------------------------------------------------------------
// Argon2id parameters (AC-1 / security.md §A02)
// ---------------------------------------------------------------------------

/// Memory cost: 19 456 KiB (m=19456).
const MEM_COST: u32 = 19_456;
/// Time cost: 2 iterations (t=2).
const TIME_COST: u32 = 2;
/// Parallelism: 1 (p=1).
const PARALLELISM: u32 = 1;

// ---------------------------------------------------------------------------
// Reference hash for timing-safe paths (AC-2 / AC-6)
// ---------------------------------------------------------------------------

/// A constant dummy password hashed once at first call.
///
/// Used in email-taken (AC-2) and unknown-email (AC-6) paths so that the
/// argon2id computation always runs, preventing timing side-channels.
static DUMMY_HASH: OnceLock<String> = OnceLock::new();

/// Return the pre-computed dummy hash (initialised exactly once).
///
/// # Panics
///
/// Panics only if argon2id fails on a fixed static input — indicates a defect
/// in the crypto library, not a user-input error.
#[allow(clippy::expect_used)]
fn dummy_hash() -> &'static str {
    DUMMY_HASH.get_or_init(|| {
        hash("__archiviste_dummy_reference_password_never_matches__")
            .expect("dummy hash: argon2id on fixed input must not fail")
    })
}

// ---------------------------------------------------------------------------
// Private: build the configured Argon2 instance
// ---------------------------------------------------------------------------

/// Build the configured `Argon2` instance.
///
/// # Panics
///
/// Panics if the compile-time constants produce invalid `Params` — a
/// programming error, not a runtime condition.
#[allow(clippy::expect_used)]
fn argon2_instance() -> Argon2<'static> {
    let params = Params::new(MEM_COST, TIME_COST, PARALLELISM, None)
        .expect("Argon2 params are compile-time constants and always valid");
    Argon2::new(argon2::Algorithm::Argon2id, Version::V0x13, params)
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/// Hash `password` with argon2id (AC-1). Returns a PHC string suitable for storage.
///
/// # Errors
///
/// Returns `HashError::Internal` if `OsRng` fails or argon2 encounters an
/// internal error (extremely unlikely for a well-formed input).
pub fn hash(password: &str) -> Result<String, HashError> {
    let salt = SaltString::generate(&mut OsRng);
    argon2_instance()
        .hash_password(password.as_bytes(), &salt)
        .map(|h| h.to_string())
        .map_err(|_| HashError::Internal)
}

/// Verify `password` against a stored `hash` string (AC-4, timing-safe).
///
/// Returns `Ok(true)` if the password matches, `Ok(false)` otherwise.
/// Use `verify_timing_safe` for paths that need constant-time comparison.
///
/// # Errors
///
/// Returns `HashError::Invalid` if `hash` is not a valid PHC string.
pub fn verify(password: &str, stored_hash: &str) -> Result<bool, HashError> {
    let parsed = PasswordHash::new(stored_hash).map_err(|_| HashError::Invalid)?;
    Ok(argon2_instance()
        .verify_password(password.as_bytes(), &parsed)
        .is_ok())
}

/// Perform a timing-safe argon2id verification against a dummy hash.
///
/// Called in the email-taken (AC-2) and unknown-email (AC-6) paths so that
/// response latency is indistinguishable from a real password verification.
/// The result is always `false` (the dummy hash never matches a real input).
pub fn verify_dummy(password: &str) {
    // The result is intentionally ignored — this runs only for its timing effect.
    let _ = verify(password, dummy_hash());
}

// ---------------------------------------------------------------------------
// Error
// ---------------------------------------------------------------------------

/// Password hashing / verification error.
#[derive(Debug)]
pub enum HashError {
    /// The provided hash string is not a valid PHC-format argon2id hash.
    Invalid,
    /// Internal argon2 failure (e.g. `OsRng` unavailable).
    Internal,
}
