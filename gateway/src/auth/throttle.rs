//! Login throttle — SEC-001 PR-b (AC-7).
//!
//! Tracks consecutive login failures per email (keyed by SHA-256 of the
//! normalised email).  After 5 failures within 15 minutes the email is locked
//! until 15 minutes after the 5th failure.  A successful login resets the counter.
//!
//! # Phase-1 constraints
//! In-memory `Mutex<HashMap>` — correct on a single Cloud Run replica (scale-to-zero
//! = effectively single instance during beta).
//! SEC-002 follow-up: move to Redis/SQL when multi-replica is needed.
//!
//! # Security
//! The key is `SHA-256(lowercase(email))` — the plain email is never stored or logged.

use sha2::{Digest, Sha256};
use std::{
    collections::HashMap,
    sync::Mutex,
    time::{Duration, Instant},
};

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

/// Maximum consecutive failures before throttling.
const MAX_FAILURES: u32 = 5;
/// Throttle window (15 minutes). Failures older than this are discarded;
/// the lockout expires `WINDOW` seconds after the 5th failure (AC-7).
const WINDOW: Duration = Duration::from_mins(15);

// ---------------------------------------------------------------------------
// Types
// ---------------------------------------------------------------------------

/// Per-email failure record.
struct FailureEntry {
    /// Count of consecutive failures.
    count: u32,
    /// Timestamp of the **first** failure in the current window (used to age out).
    first_fail_at: Instant,
    /// Timestamp of the **5th** failure (throttle expiry = `fifth_fail_at + WINDOW`).
    fifth_fail_at: Option<Instant>,
}

// ---------------------------------------------------------------------------
// Store
// ---------------------------------------------------------------------------

/// In-process throttle store.
///
/// Must be placed in `AppState` wrapped in `Arc` so it is shared across handlers.
pub struct ThrottleStore {
    inner: Mutex<HashMap<String, FailureEntry>>,
}

impl ThrottleStore {
    /// Create an empty store.
    #[must_use]
    pub fn new() -> Self {
        Self {
            inner: Mutex::new(HashMap::new()),
        }
    }

    /// Check whether the email is currently throttled.
    ///
    /// Returns `Some(retry_after_seconds)` if the caller must wait, `None` otherwise.
    pub fn is_throttled(&self, email: &str) -> Option<u64> {
        let key = email_key(email);
        let mut map = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);

        let entry = map.get_mut(&key)?;

        // Expire the entire window if the first failure is older than WINDOW.
        if entry.first_fail_at.elapsed() > WINDOW {
            map.remove(&key);
            return None;
        }

        let fifth = entry.fifth_fail_at?;
        let elapsed = fifth.elapsed();
        if elapsed >= WINDOW {
            // Throttle has expired — remove entry.
            map.remove(&key);
            return None;
        }

        if entry.count >= MAX_FAILURES {
            let remaining = WINDOW.saturating_sub(elapsed);
            return Some(remaining.as_secs().max(1));
        }

        None
    }

    /// Record a login failure for the given email.
    pub fn record_failure(&self, email: &str) {
        let key = email_key(email);
        let mut map = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        let now = Instant::now();

        let entry = map.entry(key).or_insert_with(|| FailureEntry {
            count: 0,
            first_fail_at: now,
            fifth_fail_at: None,
        });

        // Reset if the window has expired.
        if entry.first_fail_at.elapsed() > WINDOW {
            entry.count = 0;
            entry.first_fail_at = now;
            entry.fifth_fail_at = None;
        }

        entry.count += 1;

        // Record the moment the 5th failure occurs (throttle expiry anchor).
        if entry.count == MAX_FAILURES {
            entry.fifth_fail_at = Some(now);
        }
    }

    /// Reset the failure counter for the given email on successful login (AC-7).
    pub fn record_success(&self, email: &str) {
        let key = email_key(email);
        let mut map = self
            .inner
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner);
        map.remove(&key);
    }
}

impl Default for ThrottleStore {
    fn default() -> Self {
        Self::new()
    }
}

// ---------------------------------------------------------------------------
// Private helpers
// ---------------------------------------------------------------------------

/// Derive the throttle map key: `hex(SHA-256(email))`.
///
/// The email must already be normalised (lowercase, trimmed) before calling this.
fn email_key(email: &str) -> String {
    let hash = Sha256::digest(email.as_bytes());
    format!("{hash:x}")
}
